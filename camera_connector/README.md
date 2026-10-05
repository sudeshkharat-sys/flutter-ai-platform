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

## How phones and the PC find each other
1. **QR code (main way, many phones at once).** Run the connector on the PC: it prints a QR code and shows it on
   its viewer page. In the app tap **Scan QR from PC**, scan it, then tap **Start camera server** -- the phone
   registers itself and the PC starts showing its stream in a tile. Every phone does the same; each gets a tile.
   The QR holds the PC's addresses; the phone tests which one it can reach (and logs the result if none).
   (Scan *before* starting: the scanner needs the camera, which the stream also uses.)
2. **Find PC** button: the phone scans the Wi-Fi for the PC connector.
3. **PC scan / manual URL:** the connector also scans for phones, or use `--url http://PHONE-IP:8080/video`.

All three need a network where phone and PC can talk (a phone hotspot always works; some office Wi-Fi blocks it).

## Testing without a phone
`python tools/mock_phone.py` serves the same HTTP contract as the Kotlin server.
Verified: scan finds it, 1280x720 read at ~21 fps, snapshot/re-serve work, and the
connector recovers by itself after the "phone" is killed and restarted.

## Not yet verified
The Kotlin/Flutter app has not been compiled or run (no Android SDK where it was
written). Expect to fix a compile error or two on the first build -- paste the build
output or the app's debug log. Fps/heat on a real phone is the thing to measure first.
