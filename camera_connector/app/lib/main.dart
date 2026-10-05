// Camera Connector UI. All camera/streaming work is native (see native/*.kt.tpl);
// this screen only configures it, shows live stats and the debug log.

import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

const _ch = MethodChannel('camera_connector');

void main() => runApp(const ConnectorApp());

class ConnectorApp extends StatelessWidget {
  const ConnectorApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Runner Cam',
        theme: ThemeData(colorSchemeSeed: const Color(0xFF0B6FA4), useMaterial3: true),
        home: const Home(),
      );
}

class Home extends StatefulWidget {
  const Home({super.key});
  @override
  State<Home> createState() => _HomeState();
}

class _HomeState extends State<Home> {
  final _port = TextEditingController(text: '8080');
  final _name = TextEditingController(text: 'phone-1');
  (int, int) _res = (1280, 720);
  int _fps = 20;
  int _quality = 70;
  int _rotation = 0; // Surface.ROTATION_*
  bool _front = false;

  Map<String, dynamic> _s = {'running': false};
  String _log = '';
  String _msg = '';
  Timer? _timer;
  final _pcPort = TextEditingController(text: '8095');
  List<Map<String, dynamic>> _pcs = [];
  bool _scanningPc = false;
  String _pcMsg = '';

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) => _poll());
    _poll();
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _poll() async {
    try {
      final s = jsonDecode(await _ch.invokeMethod<String>('status') ?? '{}');
      final l = await _ch.invokeMethod<String>('log') ?? '';
      if (mounted) {
        setState(() {
          _s = s;
          _log = l;
          if (s['running'] == true && _msg == 'starting...') _msg = '';
        });
      }
    } catch (e) {
      if (mounted) setState(() => _msg = 'status error: $e');
    }
  }

  Future<void> _start() async {
    setState(() => _msg = 'requesting camera permission...');
    final ok = await _ch.invokeMethod<bool>('requestPermissions') ?? false;
    if (!ok) {
      setState(() => _msg = 'Camera permission denied. Allow it in Android settings > Apps > Runner Cam.');
      return;
    }
    try {
      await _ch.invokeMethod('start', {
        'port': int.tryParse(_port.text) ?? 8080,
        'width': _res.$1,
        'height': _res.$2,
        'fps': _fps,
        'quality': _quality,
        'rotation': _rotation,
        'front': _front,
        'name': _name.text.trim(),
      });
      setState(() => _msg = 'starting...');
    } on PlatformException catch (e) {
      setState(() => _msg = 'start failed: ${e.message}');
    }
  }

  Future<void> _scanPc() async {
    setState(() {
      _scanningPc = true;
      _pcMsg = 'scanning the network for the PC connector...';
    });
    try {
      final r = await _ch.invokeMethod<String>('scanPc', {'port': int.tryParse(_pcPort.text) ?? 8095}) ?? '[]';
      final list = (jsonDecode(r) as List).cast<Map<String, dynamic>>();
      setState(() {
        _pcs = list;
        _pcMsg = list.isEmpty
            ? 'No PC found. Is connector running on the PC? Same Wi-Fi? See the debug log.'
            : 'Found ${list.length} PC(s). Tap one to connect.';
      });
    } catch (e) {
      setState(() => _pcMsg = 'scan error: $e');
    } finally {
      setState(() => _scanningPc = false);
    }
  }

  Future<void> _sendToPc(Map<String, dynamic> pc) async {
    if (_s['running'] != true) {
      setState(() => _pcMsg = 'Start the camera server first, then tap the PC.');
      return;
    }
    setState(() => _pcMsg = 'asking ${pc['name']} to connect to this phone...');
    final r = await _ch.invokeMethod<String>('registerPc', {
          'ip': pc['ip'],
          'port': pc['port'],
          'phonePort': int.tryParse(_port.text) ?? 8080,
        }) ??
        '';
    setState(() => _pcMsg = 'PC replied: $r');
  }

  Future<void> _stop() async {
    await _ch.invokeMethod('stop');
    setState(() => _msg = 'stopped');
  }

  @override
  Widget build(BuildContext context) {
    final running = _s['running'] == true;
    final ips = ((_s['ips'] as List?) ?? []).cast<String>().where((i) => !i.startsWith('192.0.0.')).toList();
    final port = _s['port'] ?? _port.text;
    final err = (_s['error'] as String?) ?? '';
    return Scaffold(
      appBar: AppBar(title: const Text('Runner Cam')),
      body: SafeArea(
        child: ListView(padding: const EdgeInsets.all(12), children: [
          if (running) ...[
            Card(
              color: Colors.green.shade50,
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  const Text('STREAMING', style: TextStyle(fontWeight: FontWeight.bold)),
                  for (final ip in ips) SelectableText('http://$ip:$port/video', style: const TextStyle(fontSize: 16)),
                  const Text('Use this address on the PC (same Wi-Fi or this phone\'s hotspot).', style: TextStyle(fontSize: 11)),
                  const SizedBox(height: 6),
                  Text('${_s['fps']} fps   ${_s['size'] ?? ''}   viewers: ${_s['viewers']}'),
                  Text('encode ${_s['encodeMs']} ms   frames ${_s['encoded']}   skipped ${_s['skipped']}'),
                  Text('phone heat: ${_s['thermal'] ?? 'n/a'}   camera last frame ${_s['cameraAgeMs']} ms ago'),
                  const Text('Frames are only encoded while a viewer is connected (keeps the phone cool).',
                      style: TextStyle(fontSize: 11)),
                ]),
              ),
            ),
          ],
          if (err.isNotEmpty) Text(err, style: const TextStyle(color: Colors.red)),
          Row(children: [
            Expanded(child: TextField(controller: _port, enabled: !running, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: 'Port'))),
            const SizedBox(width: 8),
            Expanded(child: TextField(controller: _name, enabled: !running, decoration: const InputDecoration(labelText: 'Camera name'))),
          ]),
          const SizedBox(height: 8),
          Row(children: [
            Expanded(
              child: DropdownButtonFormField<(int, int)>(
                value: _res,
                decoration: const InputDecoration(labelText: 'Resolution'),
                items: const [(640, 480), (1280, 720), (1920, 1080)]
                    .map((r) => DropdownMenuItem(value: r, child: Text('${r.$1}x${r.$2}')))
                    .toList(),
                onChanged: running ? null : (v) => setState(() => _res = v!),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: DropdownButtonFormField<int>(
                value: _fps,
                decoration: const InputDecoration(labelText: 'Max fps'),
                items: [10, 15, 20, 25, 30].map((v) => DropdownMenuItem(value: v, child: Text('$v'))).toList(),
                onChanged: running ? null : (v) => setState(() => _fps = v!),
              ),
            ),
          ]),
          Row(children: [
            Expanded(
              child: DropdownButtonFormField<int>(
                value: _quality,
                decoration: const InputDecoration(labelText: 'JPEG quality'),
                items: [50, 60, 70, 80, 90].map((v) => DropdownMenuItem(value: v, child: Text('$v'))).toList(),
                onChanged: running ? null : (v) => setState(() => _quality = v!),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: DropdownButtonFormField<int>(
                value: _rotation,
                decoration: const InputDecoration(labelText: 'Phone mounted'),
                items: const [
                  DropdownMenuItem(value: 0, child: Text('Portrait')),
                  DropdownMenuItem(value: 1, child: Text('Landscape L')),
                  DropdownMenuItem(value: 3, child: Text('Landscape R')),
                  DropdownMenuItem(value: 2, child: Text('Upside down')),
                ],
                onChanged: running ? null : (v) => setState(() => _rotation = v!),
              ),
            ),
          ]),
          SwitchListTile(
            title: const Text('Front camera'),
            value: _front,
            onChanged: running ? null : (v) => setState(() => _front = v),
            dense: true,
          ),
          FilledButton.icon(
            onPressed: running ? _stop : _start,
            icon: Icon(running ? Icons.stop : Icons.videocam),
            label: Text(running ? 'Stop' : 'Start camera server'),
          ),
          if (_msg.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 6), child: Text(_msg)),
          const Divider(height: 24),
          Row(children: [
            SizedBox(width: 90, child: TextField(controller: _pcPort, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: 'PC port'))),
            const SizedBox(width: 8),
            Expanded(
              child: OutlinedButton.icon(
                onPressed: _scanningPc ? null : _scanPc,
                icon: _scanningPc ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)) : const Icon(Icons.wifi_find),
                label: const Text('Find PC'),
              ),
            ),
          ]),
          if (_pcMsg.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 6), child: Text(_pcMsg)),
          for (final pc in _pcs)
            ListTile(dense: true, leading: const Icon(Icons.computer), title: Text('${pc['name']}  ${pc['ip']}:${pc['port']}'), subtitle: const Text('tap to connect'), onTap: () => _sendToPc(pc)),
          const SizedBox(height: 12),
          Row(children: [
            const Text('DEBUG LOG', style: TextStyle(fontWeight: FontWeight.bold)),
            const Spacer(),
            TextButton(
              onPressed: () {
                Clipboard.setData(ClipboardData(text: _log));
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('log copied')));
              },
              child: const Text('Copy'),
            ),
          ]),
          Container(
            color: Colors.black,
            padding: const EdgeInsets.all(8),
            height: 260,
            child: SingleChildScrollView(
              reverse: true,
              child: SelectableText(_log.isEmpty ? '(empty)' : _log,
                  style: const TextStyle(color: Colors.lightGreenAccent, fontSize: 11, fontFamily: 'monospace')),
            ),
          ),
        ]),
      ),
    );
  }
}
