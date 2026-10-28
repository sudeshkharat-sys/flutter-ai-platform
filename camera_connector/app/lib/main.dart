// Camera Connector UI. All camera/streaming work is native (see native/*.kt.tpl).
// Home = saved PC + Connect + what the PC sees + OK / NOT OK / NOT FOUND + Submit + debug log.
// Settings page = camera settings (resolution, fps, JPEG quality, orientation, camera, autofocus) and the saved PC.

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

const _ch = MethodChannel('camera_connector');

void main() => runApp(const ConnectorApp());

class ConnectorApp extends StatelessWidget {
  const ConnectorApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Runner Cam',
        theme: ThemeData(colorSchemeSeed: const Color(0xFFDC143C), useMaterial3: true),
        home: const Home(),
      );
}

/// Everything the user can set; saved on the phone.
class Cfg {
  int port = 8080;
  String name = 'phone-1';
  int w = 1280, h = 720;
  int fps = 15; // hotspot (2.4 GHz) carries ~1 MB/s comfortably; 20+ fps queues and adds lag
  int quality = 55;
  int rotation = 0; // Surface.ROTATION_*
  bool front = false;
  bool autofocus = false;
  bool stopOnScreenOff = false; // false = the camera keeps streaming when the screen turns off
  int zoomIdx = 0;
  Map<String, dynamic>? pc; // saved PC {ip, port, name}

  Future<void> load() async {
    try {
      final p = await SharedPreferences.getInstance();
      port = p.getInt('port') ?? port;
      name = p.getString('name') ?? name;
      w = p.getInt('w') ?? w;
      h = p.getInt('h') ?? h;
      fps = p.getInt('fps') ?? fps;
      quality = p.getInt('quality') ?? quality;
      rotation = p.getInt('rotation') ?? rotation;
      front = p.getBool('front') ?? front;
      autofocus = p.getBool('autofocus') ?? autofocus;
      stopOnScreenOff = p.getBool('stopOnScreenOff') ?? stopOnScreenOff;
      zoomIdx = p.getInt('zoomIdx') ?? zoomIdx;
      final s = p.getString('pc');
      if (s != null) pc = jsonDecode(s) as Map<String, dynamic>;
    } catch (_) {}
  }

  Future<void> save() async {
    try {
      final p = await SharedPreferences.getInstance();
      await p.setInt('port', port);
      await p.setString('name', name);
      await p.setInt('w', w);
      await p.setInt('h', h);
      await p.setInt('fps', fps);
      await p.setInt('quality', quality);
      await p.setInt('rotation', rotation);
      await p.setBool('front', front);
      await p.setBool('autofocus', autofocus);
      await p.setBool('stopOnScreenOff', stopOnScreenOff);
      await p.setInt('zoomIdx', zoomIdx);
      if (pc == null) {
        await p.remove('pc');
      } else {
        await p.setString('pc', jsonEncode(pc));
      }
    } catch (_) {}
  }
}

Future<void> _dlog(String msg, [String level = 'INFO']) async {
  try {
    await _ch.invokeMethod('logLine', {'level': level, 'msg': msg});
  } catch (_) {}
}

/// Which of the PC's addresses can this phone actually reach? (HTTP /ping)
Future<Map<String, dynamic>?> _reach(List<String> ips, int port) async {
  for (final ip in ips) {
    final c = HttpClient()..connectionTimeout = const Duration(seconds: 2);
    try {
      final req = await c.getUrl(Uri.parse('http://$ip:$port/ping')).timeout(const Duration(seconds: 3));
      final res = await req.close().timeout(const Duration(seconds: 3));
      final j = jsonDecode(await res.transform(utf8.decoder).join());
      if (j is Map && j['app'] == 'runner-cam-pc') {
        await _dlog('PC reachable at $ip:$port');
        return {'ip': ip, 'port': port, 'name': '${j['name']}'};
      }
    } catch (e) {
      await _dlog('cannot reach $ip:$port -> ${e.runtimeType}', 'WARN');
    } finally {
      c.close(force: true);
    }
  }
  return null;
}

/// Scan the PC's QR code. Returns the reachable PC, or a message why not.
Future<(Map<String, dynamic>?, String)> pairByQr() async {
  final ok = await _ch.invokeMethod<bool>('requestPermissions') ?? false;
  if (!ok) return (null, 'Camera permission is needed to scan the QR code.');
  String text;
  try {
    text = await _ch.invokeMethod<String>('scanQr') ?? '';
  } on PlatformException catch (e) {
    return (null, 'QR scanner error: ${e.message}');
  }
  if (text.isEmpty) return (null, 'QR scan cancelled.');
  Map<String, dynamic> j;
  try {
    j = jsonDecode(text) as Map<String, dynamic>;
    if (j['app'] != 'runner-cam-pc') throw const FormatException('not a Runner Cam code');
  } catch (_) {
    return (null, 'That QR code is not from the Runner Cam PC.');
  }
  final ips = ((j['ips'] as List?) ?? []).cast<String>();
  final port = (j['port'] as int?) ?? 8095;
  await _dlog('QR read: PC ${j['name']} addresses=$ips port=$port');
  final pc = await _reach(ips, port);
  if (pc == null) {
    // keep the first address so it can still be saved; the PC rescans and finds the phone by itself
    return (null, 'QR read, but this phone cannot reach the PC at ${ips.join(', ')} port $port (PC firewall? same Wi-Fi?).');
  }
  return (pc, 'PC "${pc['name']}" found at ${pc['ip']}.');
}

Future<List<Map<String, dynamic>>> findPcs(int port) async {
  final r = await _ch.invokeMethod<String>('scanPc', {'port': port}) ?? '[]';
  return (jsonDecode(r) as List).cast<Map<String, dynamic>>();
}

/// Width/height from a JPEG's SOF marker (so the scan square can be mapped onto the picture).
Size? _jpegSize(Uint8List b) {
  var i = 2;
  while (i + 9 < b.length) {
    if (b[i] != 0xFF) {
      i++;
      continue;
    }
    final m = b[i + 1];
    if (m >= 0xC0 && m <= 0xCF && m != 0xC4 && m != 0xC8 && m != 0xCC) {
      return Size(((b[i + 7] << 8) | b[i + 8]).toDouble(), ((b[i + 5] << 8) | b[i + 6]).toDouble());
    }
    i += 2 + ((b[i + 2] << 8) | b[i + 3]);
  }
  return null;
}

class Home extends StatefulWidget {
  const Home({super.key});
  @override
  State<Home> createState() => _HomeState();
}

class _HomeState extends State<Home> {
  final cfg = Cfg();
  bool _loaded = false;

  Map<String, dynamic> _s = {'running': false};
  String _log = '';
  String _msg = '';
  bool _connecting = false;
  Timer? _timer;
  Timer? _previewTimer;
  Timer? _inspTimer;

  // what the PC sees (with boxes), polled as JPEG snapshots
  Uint8List? _previewJpg;
  bool _previewBusy = false;
  DateTime _previewAt = DateTime.fromMillisecondsSinceEpoch(0);
  static const _zooms = [1.0, 1.5, 2.0, 3.0];
  bool _torchAlways = false; // double tap = stays on after Submit

  // PC inspection state (polled from /api/inspection)
  String _prevInspState = '';
  Map<String, dynamic> _insp = {};
  String _lastResult = '';
  bool _inspSeeded = false;
  bool _inspBusy = false;
  int _inspFails = 0;
  DateTime _lastRetry = DateTime.fromMillisecondsSinceEpoch(0);
  bool _fullscreen = false;
  Size? _imgSize; // size of the PC picture (for the scan square)
  Offset? _dragA, _dragB; // scan square being drawn (fractions of the picture)
  String _scanMsg = '';
  DateTime _scanMsgAt = DateTime.fromMillisecondsSinceEpoch(0);
  bool _scanBusy = false;
  bool _showLog = false; // debug console overlay (bug icon), like the inspection app

  Map<String, dynamic>? get _pc => cfg.pc;
  bool get _running => _s['running'] == true;

  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    await cfg.load();
    if (!mounted) return;
    setState(() => _loaded = true);
    _timer = Timer.periodic(const Duration(seconds: 1), (_) => _poll());
    _inspTimer = Timer.periodic(const Duration(milliseconds: 700), (_) => _fetchInsp());
    _previewTimer = Timer.periodic(const Duration(milliseconds: 300), (_) => _fetchPreview());
    _poll();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _previewTimer?.cancel();
    _inspTimer?.cancel();
    super.dispose();
  }

  Future<void> _poll() async {
    try {
      final s = jsonDecode(await _ch.invokeMethod<String>('status') ?? '{}');
      final l = await _ch.invokeMethod<String>('log') ?? '';
      if (mounted) setState(() {
        _s = s;
        _log = l;
      });
    } catch (e) {
      if (mounted) setState(() => _msg = 'status error: $e');
    }
  }

  /// One snapshot of what the PC shows for this phone (boxes / result included).
  Future<void> _fetchPreview() async {
    final pc = _pc;
    if (pc == null || !_running || _previewBusy) return;
    _previewBusy = true;
    final c = HttpClient()..connectionTimeout = const Duration(seconds: 2);
    try {
      final req = await c
          .getUrl(Uri.parse('http://${pc['ip']}:${pc['port']}/preview.jpg?cam=me&t=${DateTime.now().millisecondsSinceEpoch}'))
          .timeout(const Duration(seconds: 3));
      final res = await req.close().timeout(const Duration(seconds: 3));
      if (res.statusCode == 200) {
        final b = BytesBuilder();
        await for (final chunk in res) {
          b.add(chunk);
        }
        if (mounted) setState(() {
          _previewJpg = b.takeBytes();
          _imgSize = _jpegSize(_previewJpg!) ?? _imgSize;
          _previewAt = DateTime.now();
        });
      }
    } catch (_) {
      // the old picture goes stale and is greyed out
    } finally {
      c.close(force: true);
      _previewBusy = false;
    }
  }

  /// Inspection state of the PC. Vibrates once when a result arrives; re-registers if the PC went quiet.
  Future<void> _fetchInsp() async {
    final pc = _pc;
    if (pc == null || !_running || _inspBusy) return;
    _inspBusy = true;
    final c = HttpClient()..connectionTimeout = const Duration(seconds: 2);
    try {
      final req = await c.getUrl(Uri.parse('http://${pc['ip']}:${pc['port']}/api/inspection')).timeout(const Duration(seconds: 3));
      final res = await req.close().timeout(const Duration(seconds: 3));
      final j = jsonDecode(await res.transform(utf8.decoder).join());
      if (j is Map<String, dynamic>) {
        if (_inspFails >= 3) _dlog('PC link OK again');
        _inspFails = 0;
        final r = (j['state'] == 'done' ? (j['result'] ?? '') : '').toString();
        if (!_inspSeeded) {
          // result already on the PC when this app started is old news: no buzz for it
          _inspSeeded = true;
          _lastResult = r;
        } else if (r.isNotEmpty && r != _lastResult) {
          final seen = ((j['detections'] as List?) ?? const [])
              .map((d) => '${d['name']} ${((d['conf'] ?? 0) * 100).round()}%')
              .join(', ');
          _dlog('PC result: $r | task ${j['task']} | VIN ${j['vin']} | defects ${j['defects']} | seen: ${seen.isEmpty ? 'nothing' : seen}');
          _ch.invokeMethod('vibrate', {
            'pattern': r == 'OK'
                ? [0, 700]
                : r == 'NOT OK'
                    ? [0, 250, 120, 250, 120, 250]
                    : [0, 400, 150, 400],
          });
        }
        if (r.isEmpty) _lastResult = '';
        // a scan was just armed (after the label / Start): focus once on the part, the camera was aimed elsewhere
        final st = (j['state'] ?? '').toString();
        if ((st == 'waiting' || st == 'scanning') && _prevInspState != 'waiting' && _prevInspState != 'scanning') {
          try { _ch.invokeMethod('focusNow'); } catch (_) {}
        }
        _prevInspState = st;
        if (mounted) setState(() {
          _insp = j;
          if (r.isNotEmpty) _lastResult = r;
        });
      }
    } catch (e) {
      _inspFails++;
      if (_inspFails == 3) _dlog('PC not answering at ${pc['ip']}:${pc['port']} -- ${e.runtimeType}: ${e.toString().split('\n').first} (wrong network / PC program closed / firewall?)', 'WARN');
      // PC unreachable for ~7 s: try to register again (at most every 20 s)
      if (_inspFails >= 10 && DateTime.now().difference(_lastRetry).inSeconds >= 20) {
        _lastRetry = DateTime.now();
        _dlog('PC not answering -- trying to reconnect', 'WARN');
        _sendToPc();
      }
    } finally {
      c.close(force: true);
      _inspBusy = false;
    }
  }

  Future<String?> _pcPost(String path) async {
    final pc = _pc;
    if (pc == null) return null;
    final c = HttpClient()..connectionTimeout = const Duration(seconds: 3);
    try {
      final req = await c.postUrl(Uri.parse('http://${pc['ip']}:${pc['port']}$path'));
      final res = await req.close().timeout(const Duration(seconds: 12));
      return await res.transform(utf8.decoder).join();
    } catch (e) {
      _dlog('PC request $path failed: ${e.runtimeType}', 'WARN');
      return null;
    } finally {
      c.close(force: true);
    }
  }

  /// Scan button: the PC reads the label in the scan square and says what it found.
  Future<void> _scanLabel() async {
    if (_scanBusy) return;
    setState(() {
      _scanBusy = true;
      _scanMsg = 'reading the label...';
      _scanMsgAt = DateTime.now();
    });
    final r = await _pcPost('/api/scanlabel');
    var msg = 'PC did not answer';
    var ok = false;
    if (r != null) {
      try {
        final j = jsonDecode(r) as Map<String, dynamic>;
        ok = j['ok'] == true;
        msg = '${j['message']}';
      } catch (_) {}
    }
    _dlog('scan label: $msg', ok ? 'INFO' : 'WARN');
    if (!ok) _ch.invokeMethod('vibrate', {'pattern': [0, 200, 100, 200]});
    if (mounted) setState(() {
      _scanBusy = false;
      _scanMsg = msg;
      _scanMsgAt = DateTime.now();
    });
  }

  Future<void> _startScan() async => _pcPost('/api/start');
  Future<void> _retry() async {
    setState(() => _lastResult = '');
    await _pcPost('/api/retry');
  }

  Future<void> _setRoi(double x1, double y1, double x2, double y2) async {
    await _pcPost('/api/roi?x1=$x1&y1=$y1&x2=$x2&y2=$y2');
    _dlog('scan square set');
  }

  bool get _packIdle =>
      '${_insp['state']}' == 'idle' && _insp['mode'] == 'pack' && _insp['pack'] is Map && _insp['pack']['loaded'] == true;

  /// Submit: the PC keeps the result (results.csv), then moves on to the next scan.
  Future<void> _submit() async {
    final pc = _pc;
    if (pc == null) return;
    final c = HttpClient()..connectionTimeout = const Duration(seconds: 2);
    try {
      final req = await c.postUrl(Uri.parse('http://${pc['ip']}:${pc['port']}/api/submit'));
      await (await req.close()).drain();
      _dlog('submitted $_lastResult -> next scan');
      if (mounted) setState(() {
        _lastResult = '';
        _insp = {..._insp, 'state': 'waiting', 'result': ''};
      });
      if (!_torchAlways && _s['torch'] == true) await _setTorch(false);
    } catch (e) {
      _dlog('submit failed: ${e.runtimeType}', 'WARN');
      if (mounted) setState(() => _msg = 'Submit failed: ${e.runtimeType} -- is the PC reachable?');
    } finally {
      c.close(force: true);
    }
  }

  /// Clears the on-screen console (the native log buffer is emptied too).
  Future<void> _clearLog() async {
    try {
      await _ch.invokeMethod('clearLog');
    } catch (_) {}
    setState(() => _log = '');
  }

  Future<void> _setTorch(bool on) async {
    await _ch.invokeMethod('setTorch', {'on': on});
    await _poll();
  }

  // Same gesture as the inspection app: tap = this car only, double tap = stays on for every car.
  Future<void> _torchTap() async {
    _torchAlways = false;
    await _setTorch(!(_s['torch'] == true));
  }

  Future<void> _torchDoubleTap() async {
    _torchAlways = !_torchAlways;
    await _setTorch(_torchAlways);
  }

  Future<void> _cycleZoom() async {
    final i = (cfg.zoomIdx + 1) % _zooms.length;
    await _ch.invokeMethod('setZoom', {'ratio': _zooms[i]});
    setState(() => cfg.zoomIdx = i);
    cfg.save();
  }

  Widget _torchButton() {
    final on = _s['torch'] == true;
    return GestureDetector(
      onTap: _torchTap,
      onDoubleTap: _torchDoubleTap,
      child: Container(
        width: 48,
        height: 48,
        decoration: BoxDecoration(
          color: on ? const Color(0xFFFFD700) : Colors.black54,
          shape: BoxShape.circle,
          border: Border.all(
            color: _torchAlways ? Colors.deepOrangeAccent : (on ? const Color(0xFFFFD700) : Colors.white30),
            width: _torchAlways ? 3.5 : 1.5,
          ),
        ),
        child: Icon(on ? Icons.flashlight_on : Icons.flashlight_off, color: on ? Colors.black : Colors.white, size: 24),
      ),
    );
  }

  Widget _zoomButton() {
    final zoom = _zooms[cfg.zoomIdx];
    final label = zoom == zoom.roundToDouble() ? zoom.toStringAsFixed(0) : zoom.toStringAsFixed(1);
    final zoomed = zoom > 1.01;
    return GestureDetector(
      onTap: _cycleZoom,
      child: Container(
        width: 48,
        height: 48,
        decoration: BoxDecoration(
          color: zoomed ? const Color(0xFFFFD700) : Colors.black54,
          shape: BoxShape.circle,
          border: Border.all(color: zoomed ? const Color(0xFFFFD700) : Colors.white30, width: 1.5),
        ),
        child: Center(
          child: Text('${label}x', style: TextStyle(color: zoomed ? Colors.black : Colors.white, fontWeight: FontWeight.bold, fontSize: 13)),
        ),
      ),
    );
  }

  // ---- connect ----

  Future<void> _connect() async {
    if (_pc == null) {
      setState(() => _msg = 'No PC saved yet: open Settings (gear) and scan the PC QR or Find PC.');
      return;
    }
    setState(() {
      _connecting = true;
      _msg = 'starting camera...';
    });
    try {
      final ok = await _ch.invokeMethod<bool>('requestPermissions') ?? false;
      if (!ok) {
        setState(() => _msg = 'Camera permission denied. Allow it in Android settings > Apps > Runner Cam.');
        return;
      }
      if (!_running) {
        await _startCamera();
        for (var i = 0; i < 20 && !_running; i++) {
          await Future.delayed(const Duration(milliseconds: 500));
          await _poll();
        }
        if (!_running) {
          setState(() => _msg = 'Camera server did not start: ${_s['error'] ?? ''} (see the log below)');
          return;
        }
        await Future.delayed(const Duration(seconds: 1)); // let the camera bind first
      }
      await _ch.invokeMethod('setZoom', {'ratio': _zooms[cfg.zoomIdx]});
      await _sendToPc();
    } on PlatformException catch (e) {
      setState(() => _msg = 'start failed: ${e.message}');
    } finally {
      if (mounted) setState(() => _connecting = false);
    }
  }

  Future<void> _startCamera() => _ch.invokeMethod('start', {
        'port': cfg.port,
        'width': cfg.w,
        'height': cfg.h,
        'fps': cfg.fps,
        'quality': cfg.quality,
        'rotation': cfg.rotation,
        'front': cfg.front,
        'autofocus': cfg.autofocus,
        'stopOnScreenOff': cfg.stopOnScreenOff,
        'name': cfg.name.trim(),
      });

  Future<void> _sendToPc() async {
    final pc = _pc;
    if (pc == null || !_running) return;
    if (mounted) setState(() => _msg = 'asking ${pc['name']} to connect to this phone...');
    final r = await _ch.invokeMethod<String>('registerPc', {
          'ip': pc['ip'],
          'port': pc['port'],
          'phonePort': cfg.port,
          'name': cfg.name.trim(),
        }) ??
        '';
    if (mounted) setState(() => _msg = 'PC replied: $r');
  }

  Future<void> _disconnect() async {
    await _ch.invokeMethod('stop');
    setState(() {
      _msg = 'disconnected';
      _insp = {};
      _lastResult = '';
      _previewJpg = null;
      _torchAlways = false;
    });
    await _poll();
  }

  Future<void> _openSettings() async {
    final wasRunning = _running;
    final changed = await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => SettingsPage(cfg: cfg, running: wasRunning)));
    if (changed == true && wasRunning) {
      // camera settings only take effect on a restart of the camera server
      await _disconnect();
      await Future.delayed(const Duration(milliseconds: 800));
      await _connect();
    } else if (changed == true) {
      setState(() {});
    }
  }

  // ---- UI ----

  Widget _banner({bool big = false}) {
    final st = '${_insp['state'] ?? ''}';
    final res = '${_insp['result'] ?? ''}';
    var task = '${_insp['task'] ?? ''}';
    final n = (_insp['task_n'] as num?)?.toInt() ?? 0;
    if (n > 1 && task.isNotEmpty && task != 'null') task = '$task  (${_insp['task_i']}/$n)';
    String text;
    Color col;
    final stale = DateTime.now().difference(_previewAt).inSeconds >= 5;
    if (_insp.isEmpty || _inspFails >= 4) {
      text = 'PC not answering...';
      col = Colors.grey.shade700;
    } else if (st == 'done' && res.isNotEmpty) {
      text = res;
      col = res == 'OK' ? Colors.green.shade700 : (res == 'NOT OK' ? Colors.red.shade700 : Colors.orange.shade800);
    } else if (st == 'scanning') {
      final left = _insp['seconds_left'];
      text = _insp['preview'] == true ? 'BOXES ONLY' : 'SCANNING${left != null ? '  ${left}s' : ''}';
      col = _insp['preview'] == true ? Colors.blueGrey : Colors.blue.shade700;
    } else if (st == 'ready') {
      text = 'PRESS START';
      col = Colors.green.shade700;
    } else if (st == 'waiting' || st == 'idle') {
      text = st == 'idle' ? ((_insp['pack'] is Map && _insp['pack']['loaded'] == true) ? 'SCAN THE LABEL' : 'SCAN THE VIN') : 'READY';
      col = Colors.blueGrey;
    } else if (st == 'no_model') {
      text = 'NO MODEL ON PC';
      col = Colors.grey.shade700;
    } else {
      text = st.isEmpty ? '...' : st.toUpperCase();
      col = Colors.grey.shade700;
    }
    final done = st == 'done' && res.isNotEmpty;
    final msg = '${_insp['message'] ?? ''}';
    return Container(
      width: double.infinity,
      color: col,
      padding: EdgeInsets.symmetric(vertical: big ? 8 : 4, horizontal: 12),
      child: Row(children: [
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(text, style: TextStyle(color: Colors.white, fontSize: big ? 28 : 20, fontWeight: FontWeight.bold)),
            if (_scanMsg.isNotEmpty && DateTime.now().difference(_scanMsgAt).inSeconds < 10)
              Text(_scanMsg, style: const TextStyle(color: Colors.yellowAccent, fontSize: 13, fontWeight: FontWeight.bold)),
            if (!done && msg.isNotEmpty && msg != 'null') Text(msg, style: const TextStyle(color: Colors.white70, fontSize: 12)),
            if (stale && !done) const Text('no picture from the PC', style: TextStyle(color: Colors.white70, fontSize: 11)),
          ]),
        ),
        if (done) ...[
          OutlinedButton.icon(
            style: OutlinedButton.styleFrom(foregroundColor: Colors.white, side: const BorderSide(color: Colors.white), padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14)),
            onPressed: _retry,
            icon: const Icon(Icons.replay),
            label: const Text('Retry'),
          ),
          const SizedBox(width: 8),
          FilledButton.icon(
            style: FilledButton.styleFrom(backgroundColor: Colors.white, foregroundColor: col, padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 14)),
            onPressed: _submit,
            icon: const Icon(Icons.check),
            label: const Text('Submit', style: TextStyle(fontSize: 18)),
          ),
        ] else if (st == 'ready')
          FilledButton.icon(
            style: FilledButton.styleFrom(backgroundColor: Colors.white, foregroundColor: col, padding: const EdgeInsets.symmetric(horizontal: 26, vertical: 14)),
            onPressed: _startScan,
            icon: const Icon(Icons.play_arrow),
            label: const Text('START', style: TextStyle(fontSize: 18)),
          )
        else if (st == 'scanning')
          FilledButton.icon(
            style: FilledButton.styleFrom(backgroundColor: Colors.white, foregroundColor: col, padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 14)),
            onPressed: () => _pcPost('/api/preview?on=${_insp['preview'] == true ? 0 : 1}'),
            icon: Icon(_insp['preview'] == true ? Icons.play_arrow : Icons.stop),
            label: Text(_insp['preview'] == true ? 'START' : 'STOP', style: const TextStyle(fontSize: 18)),
          )
        else if (st == 'idle' && _packIdle)
          FilledButton.icon(
            style: FilledButton.styleFrom(backgroundColor: Colors.white, foregroundColor: col, padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 14)),
            onPressed: _scanBusy ? null : _scanLabel,
            icon: _scanBusy ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)) : const Icon(Icons.qr_code_scanner),
            label: const Text('Scan', style: TextStyle(fontSize: 18)),
          ),
      ]),
    );
  }

  Widget _pcView() {
    final stale = DateTime.now().difference(_previewAt).inSeconds >= 3;
    final base = Container(
      color: Colors.black,
      alignment: Alignment.center,
      child: _previewJpg == null
          ? const Text('waiting for the PC...', style: TextStyle(color: Colors.white70))
          : Opacity(opacity: stale ? 0.35 : 1, child: Image.memory(_previewJpg!, gaplessPlayback: true, fit: BoxFit.contain)),
    );
    final size = _imgSize;
    if (!_packIdle || _previewJpg == null || size == null) return base;
    // waiting for a label: drag a square around the QR code (the PC only reads inside it), then press Scan
    return LayoutBuilder(builder: (ctx, bc) {
      final sc = math.min(bc.maxWidth / size.width, bc.maxHeight / size.height);
      final cw = size.width * sc, ch = size.height * sc;
      final ox = (bc.maxWidth - cw) / 2, oy = (bc.maxHeight - ch) / 2;
      Offset frac(Offset p) => Offset(((p.dx - ox) / cw).clamp(0.0, 1.0), ((p.dy - oy) / ch).clamp(0.0, 1.0));
      final a = _dragA, b = _dragB;
      return GestureDetector(
        onPanStart: (d) => setState(() => _dragA = _dragB = frac(d.localPosition)),
        onPanUpdate: (d) => setState(() => _dragB = frac(d.localPosition)),
        onPanEnd: (_) {
          final p = _dragA, q = _dragB;
          setState(() => _dragA = _dragB = null);
          if (p == null || q == null) return;
          final x1 = math.min(p.dx, q.dx), y1 = math.min(p.dy, q.dy), x2 = math.max(p.dx, q.dx), y2 = math.max(p.dy, q.dy);
          if (x2 - x1 > 0.03 && y2 - y1 > 0.03) _setRoi(x1, y1, x2, y2);
        },
        child: Stack(children: [
          Positioned.fill(child: base),
          if (a != null && b != null)
            Positioned(
              left: ox + math.min(a.dx, b.dx) * cw,
              top: oy + math.min(a.dy, b.dy) * ch,
              width: (a.dx - b.dx).abs() * cw,
              height: (a.dy - b.dy).abs() * ch,
              child: Container(decoration: BoxDecoration(border: Border.all(color: Colors.greenAccent, width: 3), color: Colors.greenAccent.withOpacity(0.15))),
            ),
          const Positioned(
            bottom: 36,
            left: 0,
            right: 0,
            child: Text('Drag a square around the QR code, then press Scan',
                textAlign: TextAlign.center, style: TextStyle(color: Colors.greenAccent, fontSize: 12, fontWeight: FontWeight.bold)),
          ),
        ]),
      );
    });
  }

  Widget _liveStack() {
    final fresh = DateTime.now().difference(_previewAt).inSeconds < 3;
    return Stack(children: [
      Positioned.fill(child: _pcView()),
      Positioned(
        top: 16,
        left: 16,
        child: Column(mainAxisSize: MainAxisSize.min, children: [_torchButton(), const SizedBox(height: 10), _zoomButton()]),
      ),
      Positioned(
        top: 16,
        right: 16,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          decoration: BoxDecoration(color: Colors.black.withOpacity(0.7), borderRadius: BorderRadius.circular(20)),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Container(width: 8, height: 8, decoration: BoxDecoration(color: fresh ? const Color(0xFFDC143C) : Colors.grey, shape: BoxShape.circle)),
            const SizedBox(width: 6),
            Text(fresh ? 'LIVE' : 'NO SIGNAL',
                style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.bold, letterSpacing: 0.5)),
          ]),
        ),
      ),
      Positioned(
        bottom: 12,
        left: 16,
        child: Text('${_s['fps'] ?? 0} fps  ${_s['size'] ?? ''}', style: const TextStyle(color: Colors.white54, fontSize: 11)),
      ),
      Positioned(
        bottom: 8,
        right: 12,
        child: IconButton(
          tooltip: _fullscreen ? 'Exit full screen' : 'Full screen',
          color: Colors.white70,
          onPressed: () => setState(() => _fullscreen = !_fullscreen),
          icon: Icon(_fullscreen ? Icons.fullscreen_exit : Icons.fullscreen),
        ),
      ),
      if (_showLog)
        Positioned(
          bottom: 0,
          left: 0,
          right: 0,
          height: MediaQuery.of(context).size.height * 0.8,
          child: Container(
            color: Colors.black.withOpacity(0.92),
            padding: const EdgeInsets.all(10),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                const Text('DEBUG CONSOLE', style: TextStyle(color: Colors.yellow, fontWeight: FontWeight.bold, fontSize: 14)),
                TextButton(
                  onPressed: _clearLog,
                  child: const Text('Clear', style: TextStyle(color: Colors.lightBlueAccent, fontSize: 12)),
                ),
                TextButton(
                  onPressed: () {
                    Clipboard.setData(ClipboardData(text: _log));
                    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('log copied')));
                  },
                  child: const Text('Copy', style: TextStyle(color: Colors.lightGreenAccent, fontSize: 12)),
                ),
              ]),
              const Divider(color: Colors.white24),
              Expanded(
                child: SingleChildScrollView(
                  reverse: true,
                  child: SelectableText(_log.isEmpty ? '(empty)' : _log,
                      style: const TextStyle(color: Colors.white, fontFamily: 'monospace', fontSize: 12, height: 1.3)),
                ),
              ),
            ]),
          ),
        ),
    ]);
  }

  /// Crimson header like the inspection app: back, title + subtitle, bug (debug console), settings.
  Widget _header(bool connected) {
    String clean(dynamic v) { final t = '${v ?? ''}'; return t == 'null' ? '' : t; }
    final task = clean(_insp['task']);
    final next = clean(_insp['next_task']);
    final vin = clean(_insp['vin']);
    final n = (_insp['task_n'] as num?)?.toInt() ?? 0;
    final i = (_insp['task_i'] as num?)?.toInt() ?? 0;
    final hasTask = connected && task.isNotEmpty;
    final done = connected && _insp['state'] == 'done';
    final title = hasTask ? task : (connected ? 'LIVE SCAN' : 'LIVE SCAN');
    // Live: current task (big) + next (small). Result screen adds the VIN / task count line.
    final nextLine = hasTask ? 'NEXT: ${next.isEmpty ? '\u2014' : next}' : (connected ? '' : (_pc == null ? 'no PC saved' : 'PC: ${_pc?['name']}'));
    final detail = done ? '${vin.isNotEmpty ? 'VIN: $vin' : 'PC: ${_pc?['name']}'}${n > 0 ? '  \u00b7  Task $i of $n' : ''}' : '';
    final secs = (_insp['state'] == 'scanning' && _insp['preview'] != true && _insp['seconds_left'] is num)
        ? (_insp['seconds_left'] as num).toInt()
        : null;
    // Fixed slots: the bar never changes height, whatever the PC reports.
    Widget slot(double h, String text, double size, Color c, FontWeight w) => SizedBox(
          height: h,
          child: FittedBox(fit: BoxFit.scaleDown, child: Text(text, maxLines: 1, style: TextStyle(color: c, fontSize: size, fontWeight: w))),
        );
    return Container(
      width: double.infinity,
      height: 84,
      padding: const EdgeInsets.symmetric(horizontal: 8),
      color: const Color(0xFFDC143C),
      child: Row(children: [
        TextButton.icon(
          style: TextButton.styleFrom(foregroundColor: Colors.white, padding: const EdgeInsets.symmetric(horizontal: 8)),
          icon: Icon(connected ? Icons.stop_circle : Icons.circle_outlined, color: connected ? Colors.white : Colors.white38),
          label: Text('STOP', style: TextStyle(fontWeight: FontWeight.bold, color: connected ? Colors.white : Colors.white38)),
          onPressed: connected ? _disconnect : null,
        ),
        Expanded(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            slot(28, title, 22, Colors.white, FontWeight.w900),
            slot(18, nextLine, 14, Colors.white70, FontWeight.w600),
            slot(16, detail, 12, Colors.white70, FontWeight.normal),
          ]),
        ),
        IconButton(
          padding: EdgeInsets.zero,
          constraints: const BoxConstraints(minWidth: 40, minHeight: 40),
          icon: Icon(_showLog ? Icons.bug_report : Icons.bug_report_outlined, color: Colors.white70),
          tooltip: 'Debug console',
          onPressed: () => setState(() => _showLog = !_showLog),
        ),
        // Round countdown badge, vertically centered in the bar; keeps its slot when idle.
        SizedBox(
          width: 40,
          height: 40,
          child: secs != null
              ? DecoratedBox(
                  decoration: BoxDecoration(shape: BoxShape.circle, color: secs <= 3 ? Colors.yellowAccent : Colors.white),
                  child: Center(child: Text('-$secs', style: const TextStyle(color: Color(0xFFDC143C), fontSize: 15, fontWeight: FontWeight.w900))),
                )
              : null,
        ),
        IconButton(icon: const Icon(Icons.settings, color: Colors.white), tooltip: 'Settings', onPressed: _openSettings),
      ]),
    );
  }

  Widget _disconnected() {
    final pc = _pc;
    return ListView(padding: const EdgeInsets.all(20), children: [
      const SizedBox(height: 30),
      Icon(Icons.computer, size: 64, color: Theme.of(context).colorScheme.primary),
      const SizedBox(height: 12),
      Text(pc == null ? 'No PC saved' : 'PC: ${pc['name']}', textAlign: TextAlign.center, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
      if (pc != null) Text('${pc['ip']}:${pc['port']}', textAlign: TextAlign.center),
      const SizedBox(height: 24),
      if (pc != null)
        FilledButton.icon(
          style: FilledButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 18)),
          onPressed: _connecting ? null : _connect,
          icon: _connecting
              ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
              : const Icon(Icons.link),
          label: Text(_connecting ? 'Connecting...' : 'Connect', style: const TextStyle(fontSize: 20)),
        )
      else
        FilledButton.icon(
          style: FilledButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 18)),
          onPressed: _openSettings,
          icon: const Icon(Icons.settings),
          label: const Text('Set up the PC (Settings)', style: TextStyle(fontSize: 18)),
        ),
      if (_msg.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 14), child: Text(_msg, textAlign: TextAlign.center)),
      if ('${_s['error'] ?? ''}'.isNotEmpty)
        Padding(padding: const EdgeInsets.only(top: 8), child: Text('${_s['error']}', style: const TextStyle(color: Colors.red))),
    ]);
  }

  @override
  Widget build(BuildContext context) {
    if (!_loaded) return const Scaffold(body: Center(child: CircularProgressIndicator()));
    final connected = _running && _pc != null;
    return Scaffold(
      backgroundColor: connected ? Colors.black : null,
      body: SafeArea(
        child: Column(children: [
          if (!(connected && _fullscreen)) _header(connected),
          _bannerOrNothing(connected),
          Expanded(
            child: connected
                ? _liveStack()
                : Stack(children: [
                    Positioned.fill(child: _disconnected()),
                    if (_showLog) Positioned.fill(child: _logOverlay()),
                  ]),
          ),
          if (connected && _msg.isNotEmpty)
            Container(width: double.infinity, color: Colors.black, padding: const EdgeInsets.fromLTRB(12, 2, 12, 4), child: Text(_msg, style: const TextStyle(color: Colors.white54, fontSize: 11))),
        ]),
      ),
    );
  }

  Widget _bannerOrNothing(bool connected) => connected ? _banner(big: _fullscreen) : const SizedBox.shrink();

  Widget _logOverlay() => Container(
        color: Colors.black.withOpacity(0.92),
        padding: const EdgeInsets.all(10),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
            const Text('DEBUG CONSOLE', style: TextStyle(color: Colors.yellow, fontWeight: FontWeight.bold, fontSize: 14)),
            TextButton(
              onPressed: _clearLog,
              child: const Text('Clear', style: TextStyle(color: Colors.lightBlueAccent, fontSize: 12)),
            ),
            TextButton(
              onPressed: () {
                Clipboard.setData(ClipboardData(text: _log));
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('log copied')));
              },
              child: const Text('Copy', style: TextStyle(color: Colors.lightGreenAccent, fontSize: 12)),
            ),
          ]),
          const Divider(color: Colors.white24),
          Expanded(
            child: SingleChildScrollView(
              reverse: true,
              child: SelectableText(_log.isEmpty ? '(empty)' : _log,
                  style: const TextStyle(color: Colors.white, fontFamily: 'monospace', fontSize: 12, height: 1.3)),
            ),
          ),
        ]),
      );
}

/// Camera settings + saved PC. Pops `true` when something changed.
class SettingsPage extends StatefulWidget {
  final Cfg cfg;
  final bool running;
  const SettingsPage({super.key, required this.cfg, required this.running});
  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  Cfg get c => widget.cfg;
  late final _port = TextEditingController(text: '${c.port}');
  late final _name = TextEditingController(text: c.name);
  final _pcPort = TextEditingController(text: '8095');
  bool _dirty = false; // needs a camera restart
  bool _busy = false;
  String _msg = '';
  List<Map<String, dynamic>> _found = [];

  void _edit(void Function() f) {
    setState(() {
      f();
      _dirty = true;
    });
  }

  Future<void> _save() async {
    c.port = int.tryParse(_port.text) ?? c.port;
    c.name = _name.text.trim().isEmpty ? c.name : _name.text.trim();
    await c.save();
    if (mounted) Navigator.of(context).pop(_dirty);
  }

  Future<void> _setAutofocus(bool v) async {
    setState(() => c.autofocus = v);
    await c.save();
    try {
      await _ch.invokeMethod('setAutofocus', {'on': v}); // live, no restart needed
    } catch (_) {}
  }

  void _savePc(Map<String, dynamic> pc) {
    setState(() {
      c.pc = pc;
      _dirty = true;
    });
    c.save();
  }

  Future<void> _scanQr() async {
    if (widget.running) {
      setState(() => _msg = 'Disconnect first (the QR scanner needs the camera), then scan.');
      return;
    }
    setState(() => _busy = true);
    final (pc, msg) = await pairByQr();
    if (pc != null) _savePc(pc);
    if (mounted) setState(() {
      _busy = false;
      _msg = pc != null ? '$msg Saved.' : msg;
    });
  }

  Future<void> _find() async {
    setState(() {
      _busy = true;
      _msg = 'scanning the network for the PC...';
    });
    try {
      final l = await findPcs(int.tryParse(_pcPort.text) ?? 8095);
      setState(() {
        _found = l;
        _msg = l.isEmpty ? 'No PC found. Is pc_runner.exe running? Same Wi-Fi?' : 'Found ${l.length} PC(s). Tap one to save it.';
      });
    } catch (e) {
      setState(() => _msg = 'scan error: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final pc = c.pc;
    return PopScope(
      canPop: false,
      onPopInvoked: (didPop) {
        if (!didPop) _save();
      },
      child: Scaffold(
        appBar: AppBar(title: const Text('Settings'), actions: [TextButton(onPressed: _save, child: const Text('Save'))]),
        body: ListView(padding: const EdgeInsets.all(14), children: [
          const Text('PC', style: TextStyle(fontWeight: FontWeight.bold)),
          Card(
            child: ListTile(
              leading: const Icon(Icons.computer),
              title: Text(pc == null ? 'No PC saved' : '${pc['name']}'),
              subtitle: Text(pc == null ? 'Scan the QR shown by pc_runner.exe or use Find PC' : '${pc['ip']}:${pc['port']}'),
              trailing: pc == null ? null : IconButton(tooltip: 'Forget', icon: const Icon(Icons.delete_outline), onPressed: () => setState(() {
                    c.pc = null;
                    _dirty = true;
                    c.save();
                  })),
            ),
          ),
          Row(children: [
            Expanded(child: FilledButton.tonalIcon(onPressed: _busy ? null : _scanQr, icon: const Icon(Icons.qr_code_scanner), label: const Text('Scan PC QR'))),
            const SizedBox(width: 8),
            SizedBox(width: 80, child: TextField(controller: _pcPort, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: 'PC port'))),
            const SizedBox(width: 8),
            OutlinedButton.icon(onPressed: _busy ? null : _find, icon: const Icon(Icons.wifi_find), label: const Text('Find')),
          ]),
          if (_msg.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 6), child: Text(_msg)),
          for (final f in _found)
            ListTile(dense: true, leading: const Icon(Icons.computer), title: Text('${f['name']}  ${f['ip']}:${f['port']}'), subtitle: const Text('tap to save'), onTap: () => _savePc(f)),
          const Divider(height: 28),
          const Text('Camera', style: TextStyle(fontWeight: FontWeight.bold)),
          if (widget.running) const Text('Changes restart the camera when you tap Save.', style: TextStyle(fontSize: 11)),
          Row(children: [
            Expanded(child: TextField(controller: _port, keyboardType: TextInputType.number, onChanged: (_) => _dirty = true, decoration: const InputDecoration(labelText: 'Camera port'))),
            const SizedBox(width: 8),
            Expanded(child: TextField(controller: _name, onChanged: (_) => _dirty = true, decoration: const InputDecoration(labelText: 'Camera name'))),
          ]),
          const SizedBox(height: 8),
          Row(children: [
            Expanded(
              child: DropdownButtonFormField<(int, int)>(
                value: (c.w, c.h),
                decoration: const InputDecoration(labelText: 'Resolution'),
                items: const [(640, 480), (1280, 720), (1920, 1080)].map((r) => DropdownMenuItem(value: r, child: Text('${r.$1}x${r.$2}'))).toList(),
                onChanged: (v) => _edit(() {
                  c.w = v!.$1;
                  c.h = v.$2;
                }),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: DropdownButtonFormField<int>(
                value: c.fps,
                decoration: const InputDecoration(labelText: 'Max fps'),
                items: [10, 15, 20, 25, 30].map((v) => DropdownMenuItem(value: v, child: Text('$v'))).toList(),
                onChanged: (v) => _edit(() => c.fps = v!),
              ),
            ),
          ]),
          Row(children: [
            Expanded(
              child: DropdownButtonFormField<int>(
                value: c.quality,
                decoration: const InputDecoration(labelText: 'JPEG quality'),
                items: [40, 50, 55, 60, 70, 80, 90].map((v) => DropdownMenuItem(value: v, child: Text('$v'))).toList(),
                onChanged: (v) => _edit(() => c.quality = v!),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: DropdownButtonFormField<int>(
                value: c.rotation,
                decoration: const InputDecoration(labelText: 'Phone mounted'),
                items: const [
                  DropdownMenuItem(value: 0, child: Text('Portrait')),
                  DropdownMenuItem(value: 1, child: Text('Landscape L')),
                  DropdownMenuItem(value: 3, child: Text('Landscape R')),
                  DropdownMenuItem(value: 2, child: Text('Upside down')),
                ],
                onChanged: (v) => _edit(() => c.rotation = v!),
              ),
            ),
          ]),
          SwitchListTile(
            title: const Text('Stop streaming when the screen is off'),
            subtitle: const Text('Off (default): the camera keeps streaming with the screen off. On: turning the screen off stops the camera (saves battery and heat).'),
            value: c.stopOnScreenOff,
            onChanged: (v) => _edit(() => c.stopOnScreenOff = v),
            dense: true,
          ),
          SwitchListTile(title: const Text('Front camera'), value: c.front, onChanged: (v) => _edit(() => c.front = v), dense: true),
          SwitchListTile(
            title: const Text('Autofocus'),
            subtitle: const Text('On: refocus on the centre of the picture every 8 s. Off: the camera\'s own continuous focus.'),
            value: c.autofocus,
            onChanged: _setAutofocus,
            dense: true,
          ),
          ListTile(
            leading: const Icon(Icons.wifi_tethering),
            title: const Text('Hotspot: use 5 GHz'),
            subtitle: const Text('Opens the phone\'s hotspot settings. Set "AP band" to 5 GHz, then turn the hotspot off and on. Much less lag than 2.4 GHz.'),
            onTap: () => _ch.invokeMethod('openHotspotSettings'),
            dense: true,
          ),
          const Divider(height: 28),
          const Text('Torch: tap = this car only, double tap = stays on for every car (orange ring).', style: TextStyle(fontSize: 12)),
        ]),
      ),
    );
  }
}
