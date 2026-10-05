# Camera Connector -- phone camera as a smooth CCTV-style feed

Replaces the still-photo approach of `pc_runner/` (kept untouched as a fallback).

```
 phone app (native CameraX -> JPEG -> MJPEG HTTP server, like IP Webcam)
        http://<phone-ip>:8080/video
                   |  Wi-Fi / phone hotspot
 PC runner_connector.exe: scan -> cv2.VideoCapture(url) -> viewer + debug log
```

Why smooth: frames never touch Dart. Android's CameraX hands RGBA frames to native
code which compresses them with the platform JPEG encoder. Frames are only
encoded while someone is watching, capped at "Max fps", and the service keeps the
camera alive with the screen off -- all to keep the phone cool.

## Phone app (`app/`)
Flutter UI + native Kotlin (`app/native/*.kt.tpl`). No Flutter camera plugin.

    cd app
    build_apk.bat          # = python setup_android.py + flutter pub get + flutter build apk --release

`setup_android.py` runs `flutter create` if `android/` is missing, installs the Kotlin
files, manifest entries (camera, foreground service) and CameraX dependencies, and is
safe to re-run. APK: `app/build/app/outputs/flutter-apk/app-release.apk`.
In the app: pick resolution / fps / quality / mounting orientation -> **Start camera
server**. It shows the URL(s) and live fps, encode ms, viewers; a DEBUG LOG with Copy.
Same data over HTTP: `/status`, `/log`, `/snapshot.jpg`, `/ping`.

## PC (`pc/`)
    python connector.py                      # scan, connect, open viewer
    python connector.py --url http://192.168.1.50:8080/video
    python connector.py --window             # also an OpenCV window
    build_exe.bat                            # -> dist\runner_connector.exe (no install needed)

Scan = HTTP `/ping` sweep of the PC's /24 networks + UDP beacon (port 8092). Debug is
on by default; log goes to the console, the viewer page and `connector_debug.log`.
The reader thread keeps only the newest frame (no growing latency) and reconnects
automatically. Windows may ask to allow network access once: choose Private networks.

## Two ways to connect
1. **Phone finds the PC (use this first):** run `connector.py` / `runner_connector.exe` on the PC,
   start the camera server in the app, tap **Find PC**, tap the PC. The phone tells the PC where its
   stream is; the PC replies whether it can reach it (or exactly why not) and starts reading.
2. **PC finds the phone:** the connector also scans automatically, or use `--url http://PHONE-IP:8080/video`.

Both directions need a network that lets phone and PC talk to each other (not blocked by Wi-Fi client
isolation / security software). If one direction works and the other doesn't, the debug logs say which.

## Testing without a phone
`python tools/mock_phone.py` serves the same HTTP contract as the Kotlin server.
Verified: scan finds it, 1280x720 read at ~21 fps, snapshot/re-serve work, and the
connector recovers by itself after the "phone" is killed and restarted.

## Not yet verified
The Kotlin/Flutter app has not been compiled or run (no Android SDK where it was
written). Expect to fix a compile error or two on the first build -- paste the build
output or the app's debug log. Fps/heat on a real phone is the thing to measure first.
