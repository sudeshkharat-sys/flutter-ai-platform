// Throwaway connectivity test: camera -> JPEG -> WebSocket, on repeat.
// See ../README.md and ../../LIVE_FEED_RUNNER_PLAN.md. No detection here.

import 'dart:async';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

late List<CameraDescription> _cameras;

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  _cameras = await availableCameras();
  runApp(const MaterialApp(home: StreamTestScreen()));
}

class StreamTestScreen extends StatefulWidget {
  const StreamTestScreen({super.key});

  @override
  State<StreamTestScreen> createState() => _StreamTestScreenState();
}

class _StreamTestScreenState extends State<StreamTestScreen> {
  final _ipController = TextEditingController(text: '192.168.43.1');
  final _portController = TextEditingController(text: '8090');

  CameraController? _cameraController;
  WebSocketChannel? _channel;
  bool _streaming = false;
  bool _connectionConfirmed = false;
  int _framesSent = 0;
  int _framesAcked = 0;
  String _status = 'idle';

  @override
  void dispose() {
    _stop();
    _ipController.dispose();
    _portController.dispose();
    super.dispose();
  }

  Future<void> _start() async {
    final ip = _ipController.text.trim();
    final port = _portController.text.trim();
    final uri = Uri.parse('ws://$ip:$port/stream');

    setState(() => _status = 'connecting to $uri ...');

    try {
      _channel = WebSocketChannel.connect(uri);

      // WebSocketChannel.connect() returns immediately without actually
      // waiting for the handshake to succeed -- awaiting `ready` is what
      // surfaces a real connection failure instead of silently pretending
      // it worked.
      await _channel!.ready;
      _connectionConfirmed = true;

      _channel!.stream.listen(
        (message) {
          // runner.py sends back "ack:<n>" after each frame it decodes,
          // so a rising _framesAcked means frames are really arriving,
          // not just being handed to the socket.
          if (message is String && message.startsWith('ack:')) {
            setState(() => _framesAcked++);
          }
        },
        onDone: () {
          setState(() {
            _status = 'connection closed by server';
            _streaming = false;
          });
        },
        onError: (e) {
          setState(() {
            _status = 'connection error: $e';
            _streaming = false;
          });
        },
      );

      _cameraController = CameraController(
        _cameras.first,
        ResolutionPreset.low,
        enableAudio: false,
      );
      await _cameraController!.initialize();

      setState(() {
        _streaming = true;
        _status = 'streaming to $uri';
      });

      _captureLoop();
    } catch (e) {
      setState(() => _status = 'connect failed: $e');
    }
  }

  Future<void> _captureLoop() async {
    while (_streaming && _cameraController != null) {
      try {
        final picture = await _cameraController!.takePicture();
        final bytes = await picture.readAsBytes();
        _channel?.sink.add(bytes);
        _framesSent++;
        if (mounted) setState(() {});
      } catch (e) {
        setState(() => _status = 'send error: $e');
        break;
      }
      await Future.delayed(const Duration(milliseconds: 200));
    }
  }

  Future<void> _stop() async {
    _streaming = false;
    _connectionConfirmed = false;
    await _cameraController?.stopImageStream().catchError((_) {});
    await _cameraController?.dispose();
    await _channel?.sink.close();
    _cameraController = null;
    _channel = null;
    if (mounted) setState(() => _status = 'stopped');
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Connectivity test (step 1)')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              controller: _ipController,
              decoration: const InputDecoration(labelText: 'PC IP'),
              enabled: !_streaming,
            ),
            TextField(
              controller: _portController,
              decoration: const InputDecoration(labelText: 'Port'),
              enabled: !_streaming,
            ),
            const SizedBox(height: 16),
            ElevatedButton(
              onPressed: _streaming ? _stop : _start,
              child: Text(_streaming ? 'Stop' : 'Start streaming'),
            ),
            const SizedBox(height: 16),
            Text('Status: $_status'),
            Text('Connection confirmed: $_connectionConfirmed'),
            Text('Frames sent: $_framesSent'),
            Text('Frames acked by PC: $_framesAcked'),
          ],
        ),
      ),
    );
  }
}
