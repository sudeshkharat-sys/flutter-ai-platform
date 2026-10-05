// Runner Cam: phone camera -> JPEG -> WebSocket -> pc_runner/runner.py.
// Finds the PC by scanning the Wi-Fi/hotspot subnet (and listening for the
// runner's UDP beacon), streams with auto-reconnect, and keeps a debug log
// that is also forwarded to the PC. See ../README.md.

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:camera/camera.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:wakelock_plus/wakelock_plus.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

const int kBeaconPort = 8091;
const String kAppId = 'runner-cam';

// ---------------------------------------------------------------- logging --

class Log {
  static final lines = ValueNotifier<List<String>>([]);
  static void Function(String level, String msg)? forward; // to the PC

  static void add(String level, String msg) {
    final t = DateTime.now().toIso8601String().substring(11, 23);
    final next = [...lines.value, '$t [$level] $msg'];
    lines.value = next.length > 400 ? next.sublist(next.length - 400) : next;
    debugPrint('[RunnerCam] $level $msg');
    try {
      forward?.call(level, msg);
    } catch (_) {}
  }

  static void i(String m) => add('INFO', m);
  static void w(String m) => add('WARN', m);
  static void e(String m) => add('ERROR', m);
}

Future<void> main() async {
  runZonedGuarded(() async {
    WidgetsFlutterBinding.ensureInitialized();
    FlutterError.onError = (d) => Log.e('Flutter: ${d.exceptionAsString()}');
    runApp(const RunnerCamApp());
  }, (error, stack) => Log.e('Uncaught: $error\n$stack'));
}

class RunnerCamApp extends StatelessWidget {
  const RunnerCamApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Runner Cam',
        theme: ThemeData(colorSchemeSeed: const Color(0xFF0B6FA4), useMaterial3: true),
        home: const HomeScreen(),
      );
}

// -------------------------------------------------------------- discovery --

class Runner {
  final String ip;
  final int port;
  final String name;
  Runner(this.ip, this.port, this.name);
  @override
  bool operator ==(Object o) => o is Runner && o.ip == ip && o.port == port;
  @override
  int get hashCode => Object.hash(ip, port);
}

class Discovery {
  /// Probe http://ip:port/ping on every host of each local /24 subnet.
  static Future<List<Runner>> sweep(int port, {void Function(String)? progress}) async {
    final found = <Runner>{};
    final ifaces = await NetworkInterface.list(type: InternetAddressType.IPv4);
    final bases = <String>{};
    for (final i in ifaces) {
      for (final a in i.addresses) {
        Log.i('interface ${i.name}: ${a.address}');
        if (!a.isLoopback) bases.add(a.address.substring(0, a.address.lastIndexOf('.')));
      }
    }
    if (bases.isEmpty) {
      Log.e('scan: phone has no IPv4 address -- is Wi-Fi / hotspot on?');
      return [];
    }
    for (final base in bases) {
      Log.i('scan: probing $base.1-254 on port $port');
      progress?.call('scanning $base.x ...');
      final hosts = List.generate(254, (i) => '$base.${i + 1}');
      for (var s = 0; s < hosts.length; s += 64) {
        await Future.wait(hosts.skip(s).take(64).map((h) async {
          final r = await probe(h, port);
          if (r != null) found.add(r);
        }));
      }
    }
    Log.i('scan done: ${found.length} runner(s) found');
    return found.toList();
  }

  static Future<Runner?> probe(String ip, int port, {Duration timeout = const Duration(milliseconds: 700)}) async {
    final client = HttpClient()..connectionTimeout = timeout;
    try {
      final req = await client.getUrl(Uri.parse('http://$ip:$port/ping')).timeout(timeout);
      final res = await req.close().timeout(timeout);
      final body = await res.transform(utf8.decoder).join().timeout(timeout);
      final j = jsonDecode(body);
      if (j is Map && j['app'] == kAppId) {
        Log.i('scan: runner at $ip:$port (${j['name']})');
        return Runner(ip, (j['port'] as int?) ?? port, '${j['name']}');
      }
    } catch (_) {
      // not a runner / nothing listening -- expected for almost every host
    } finally {
      client.close(force: true);
    }
    return null;
  }

  /// Listen for the runner's UDP broadcast for [seconds].
  static Future<List<Runner>> listenBeacon({int seconds = 4}) async {
    final found = <Runner>{};
    RawDatagramSocket? sock;
    try {
      sock = await RawDatagramSocket.bind(InternetAddress.anyIPv4, kBeaconPort, reuseAddress: true);
      sock.listen((e) {
        if (e != RawSocketEvent.read) return;
        final d = sock!.receive();
        if (d == null) return;
        try {
          final j = jsonDecode(utf8.decode(d.data));
          if (j['app'] == kAppId) found.add(Runner(j['ip'], j['port'], '${j['name']}'));
        } catch (_) {}
      });
      await Future.delayed(Duration(seconds: seconds));
    } catch (e) {
      Log.w('beacon listen failed (ok, sweep still works): $e');
    } finally {
      sock?.close();
    }
    Log.i('beacon: ${found.length} runner(s) heard');
    return found.toList();
  }
}

// ------------------------------------------------------------------- UI --

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _ip = TextEditingController();
  final _port = TextEditingController(text: '8090');
  final _name = TextEditingController(text: 'phone-1');

  List<CameraDescription> _cameras = [];
  int _cameraIndex = 0;
  ResolutionPreset _preset = ResolutionPreset.medium;
  int _intervalMs = 150;

  CameraController? _cam;
  WebSocketChannel? _ws;
  bool _running = false; // user wants streaming
  bool _scanning = false;
  String _status = 'idle';
  int _sent = 0, _acked = 0, _gen = 0;
  List<Runner> _runners = [];
  bool _showLog = true;

  @override
  void initState() {
    super.initState();
    Log.forward = (level, msg) {
      try {
        _ws?.sink.add(jsonEncode({'type': 'log', 'level': level, 'msg': msg}));
      } catch (_) {}
    };
    _loadCameras();
    _scan();
  }

  @override
  void dispose() {
    _stop();
    Log.forward = null;
    super.dispose();
  }

  Future<void> _loadCameras() async {
    try {
      _cameras = await availableCameras();
      Log.i('cameras: ${_cameras.map((c) => '${c.name}(${c.lensDirection.name})').join(', ')}');
      if (_cameras.isEmpty) Log.e('no camera found on this phone');
      setState(() {});
    } catch (e) {
      Log.e('availableCameras failed: $e');
    }
  }

  Future<void> _scan() async {
    if (_scanning) return;
    setState(() {
      _scanning = true;
      _status = 'scanning network...';
    });
    final port = int.tryParse(_port.text) ?? 8090;
    try {
      final results = await Future.wait([
        Discovery.listenBeacon(),
        Discovery.sweep(port),
      ]);
      final all = {...results[0], ...results[1]}.toList();
      setState(() {
        _runners = all;
        _status = all.isEmpty
            ? 'no runner found -- same Wi-Fi? runner.exe running? firewall allowed? (see debug log)'
            : 'found ${all.length} runner(s) -- tap one';
      });
      if (all.length == 1 && _ip.text.isEmpty) _select(all.first);
    } catch (e) {
      Log.e('scan failed: $e');
      setState(() => _status = 'scan failed: $e');
    } finally {
      setState(() => _scanning = false);
    }
  }

  void _select(Runner r) {
    setState(() {
      _ip.text = r.ip;
      _port.text = '${r.port}';
    });
    Log.i('selected runner ${r.name} ${r.ip}:${r.port}');
  }

  // ---- streaming ----

  Future<void> _start() async {
    if (_ip.text.trim().isEmpty) {
      setState(() => _status = 'enter or scan for the PC address first');
      return;
    }
    // Camera first: this is what triggers the permission prompt.
    if (!await _initCamera()) return;
    _running = true;
    WakelockPlus.enable();
    final gen = ++_gen;
    setState(() => _status = 'starting...');
    _session(gen);
  }

  Future<bool> _initCamera() async {
    try {
      if (_cameras.isEmpty) await _loadCameras();
      if (_cameras.isEmpty) {
        setState(() => _status = 'no camera available');
        return false;
      }
      await _cam?.dispose();
      _cam = CameraController(_cameras[_cameraIndex % _cameras.length], _preset, enableAudio: false);
      await _cam!.initialize();
      Log.i('camera ready: ${_cam!.value.previewSize}');
      return true;
    } on CameraException catch (e) {
      Log.e('camera error ${e.code}: ${e.description}');
      setState(() => _status = 'camera error: ${e.code} (permission denied?)');
      return false;
    } catch (e) {
      Log.e('camera init failed: $e');
      setState(() => _status = 'camera init failed: $e');
      return false;
    }
  }

  /// Connect + capture loop; reconnects until the user taps Stop.
  Future<void> _session(int gen) async {
    var attempt = 0;
    while (_running && gen == _gen) {
      final uri = Uri(
        scheme: 'ws',
        host: _ip.text.trim(),
        port: int.tryParse(_port.text) ?? 8090,
        path: '/stream',
        queryParameters: {'name': _name.text.trim()},
      );
      try {
        attempt++;
        setState(() => _status = 'connecting to $uri (try $attempt)');
        Log.i('connecting $uri');
        final ws = WebSocketChannel.connect(uri);
        await ws.ready.timeout(const Duration(seconds: 6));
        _ws = ws;
        attempt = 0;
        final closed = Completer<void>();
        ws.stream.listen(
          (m) {
            if (m is String && m.startsWith('ack:')) {
              if (mounted) setState(() => _acked++);
            } else if (m is String && m.startsWith('err:')) {
              Log.w('PC rejected frame: $m');
            }
          },
          onDone: () => closed.complete(),
          onError: (e) {
            Log.e('socket error: $e');
            if (!closed.isCompleted) closed.complete();
          },
        );
        ws.sink.add(jsonEncode({
          'type': 'hello',
          'platform': Platform.operatingSystem,
          'camera': _cameras.isEmpty ? null : _cameras[_cameraIndex % _cameras.length].name,
          'preset': _preset.name,
          'intervalMs': _intervalMs,
        }));
        Log.i('connected');
        if (mounted) setState(() => _status = 'streaming to $uri');

        while (_running && gen == _gen && !closed.isCompleted) {
          final t0 = DateTime.now();
          try {
            final pic = await _cam!.takePicture();
            final bytes = await pic.readAsBytes();
            ws.sink.add(bytes);
            if (mounted) setState(() => _sent++);
            File(pic.path).delete().catchError((_) => File(pic.path));
          } catch (e) {
            Log.e('capture/send error: $e');
            break;
          }
          final spent = DateTime.now().difference(t0).inMilliseconds;
          if (spent < _intervalMs) await Future.delayed(Duration(milliseconds: _intervalMs - spent));
        }
        _ws = null;
        await ws.sink.close().catchError((_) {});
        if (_running && gen == _gen) Log.w('connection lost, reconnecting...');
      } on TimeoutException {
        Log.e('connect timed out: $uri -- runner not reachable (wrong IP/port, firewall, different Wi-Fi?)');
      } catch (e) {
        Log.e('connect failed: $e');
      }
      _ws = null;
      if (_running && gen == _gen) {
        if (mounted) setState(() => _status = 'reconnecting in 2s... ($attempt)');
        await Future.delayed(const Duration(seconds: 2));
      }
    }
  }

  Future<void> _stop() async {
    _running = false;
    _gen++;
    WakelockPlus.disable();
    await Future.delayed(const Duration(milliseconds: 300)); // let an in-flight capture finish
    await _cam?.dispose();
    _cam = null;
    try {
      await _ws?.sink.close();
    } catch (_) {}
    _ws = null;
    if (mounted) setState(() => _status = 'stopped');
  }

  @override
  Widget build(BuildContext context) {
    final streaming = _running;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Runner Cam'),
        actions: [
          IconButton(
            tooltip: 'Debug log',
            icon: Icon(_showLog ? Icons.bug_report : Icons.bug_report_outlined),
            onPressed: () => setState(() => _showLog = !_showLog),
          ),
        ],
      ),
      body: SafeArea(
        child: Column(children: [
          Expanded(
            flex: 3,
            child: ListView(padding: const EdgeInsets.all(12), children: [
              if (_cam != null && _cam!.value.isInitialized)
                SizedBox(height: 180, child: ClipRRect(borderRadius: BorderRadius.circular(8), child: CameraPreview(_cam!))),
              Row(children: [
                Expanded(flex: 3, child: TextField(controller: _ip, enabled: !streaming, decoration: const InputDecoration(labelText: 'PC IP'), keyboardType: TextInputType.number)),
                const SizedBox(width: 8),
                Expanded(child: TextField(controller: _port, enabled: !streaming, decoration: const InputDecoration(labelText: 'Port'), keyboardType: TextInputType.number)),
              ]),
              TextField(controller: _name, enabled: !streaming, decoration: const InputDecoration(labelText: 'Camera name')),
              const SizedBox(height: 8),
              Row(children: [
                Expanded(
                  child: DropdownButtonFormField<ResolutionPreset>(
                    value: _preset,
                    decoration: const InputDecoration(labelText: 'Quality'),
                    items: [ResolutionPreset.low, ResolutionPreset.medium, ResolutionPreset.high, ResolutionPreset.veryHigh]
                        .map((p) => DropdownMenuItem(value: p, child: Text(p.name)))
                        .toList(),
                    onChanged: streaming ? null : (v) => setState(() => _preset = v!),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: DropdownButtonFormField<int>(
                    value: _intervalMs,
                    decoration: const InputDecoration(labelText: 'Frame gap'),
                    items: [50, 100, 150, 250, 500].map((v) => DropdownMenuItem(value: v, child: Text('$v ms'))).toList(),
                    onChanged: streaming ? null : (v) => setState(() => _intervalMs = v!),
                  ),
                ),
                IconButton(
                  tooltip: 'Switch camera',
                  icon: const Icon(Icons.cameraswitch),
                  onPressed: streaming || _cameras.length < 2 ? null : () => setState(() => _cameraIndex = (_cameraIndex + 1) % _cameras.length),
                ),
              ]),
              const SizedBox(height: 8),
              Row(children: [
                Expanded(
                  child: FilledButton.icon(
                    onPressed: streaming ? _stop : _start,
                    icon: Icon(streaming ? Icons.stop : Icons.videocam),
                    label: Text(streaming ? 'Stop' : 'Start streaming'),
                  ),
                ),
                const SizedBox(width: 8),
                OutlinedButton.icon(
                  onPressed: _scanning || streaming ? null : _scan,
                  icon: _scanning ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)) : const Icon(Icons.wifi_find),
                  label: const Text('Scan'),
                ),
              ]),
              const SizedBox(height: 8),
              Text(_status, style: Theme.of(context).textTheme.bodyMedium),
              Text('sent $_sent   acked by PC $_acked'),
              for (final r in _runners)
                ListTile(
                  dense: true,
                  leading: const Icon(Icons.computer),
                  title: Text('${r.name}  ${r.ip}:${r.port}'),
                  onTap: streaming ? null : () => _select(r),
                ),
            ]),
          ),
          if (_showLog)
            Expanded(
              flex: 2,
              child: Container(
                color: Colors.black,
                width: double.infinity,
                child: Column(children: [
                  Row(children: [
                    const Padding(padding: EdgeInsets.only(left: 8), child: Text('DEBUG LOG', style: TextStyle(color: Colors.white70, fontSize: 11))),
                    const Spacer(),
                    TextButton(
                      onPressed: () {
                        Clipboard.setData(ClipboardData(text: Log.lines.value.join('\n')));
                        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('log copied')));
                      },
                      child: const Text('Copy'),
                    ),
                    TextButton(onPressed: () => Log.lines.value = [], child: const Text('Clear')),
                  ]),
                  Expanded(
                    child: ValueListenableBuilder<List<String>>(
                      valueListenable: Log.lines,
                      builder: (_, l, __) => ListView.builder(
                        reverse: true,
                        padding: const EdgeInsets.symmetric(horizontal: 8),
                        itemCount: l.length,
                        itemBuilder: (_, i) {
                          final line = l[l.length - 1 - i];
                          final color = line.contains('[ERROR]') ? Colors.redAccent : line.contains('[WARN]') ? Colors.amber : Colors.lightGreenAccent;
                          return Text(line, style: TextStyle(color: color, fontSize: 11, fontFamily: 'monospace'));
                        },
                      ),
                    ),
                  ),
                ]),
              ),
            ),
        ]),
      ),
    );
  }
}
