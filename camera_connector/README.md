# Camera Connector -- phone camera as a smooth CCTV-style feed

Replaces the still-photo approach of `pc_runner/` (kept untouched as a fallback).

```
 phone app (native CameraX -> JPEG -> MJPEG HTTP server, like IP Webcam)
        http://<phone-ip>:8080/video
                   |  Wi-Fi / phone hotspot
 PC pc_runner.exe: scan -> cv2.VideoCapture(url) -> viewer + debug log
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
    build_exe.bat                            # -> dist\pc_runner.exe (no install needed)

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

## Inspection (YOLO .pt) in the connector
Put `pc_runner.exe` and your `.pt` files in one folder (or a `models/` subfolder). In the viewer's top bar:
- **model dropdown** -- pick the `.pt` (remembered in `runner_config.json`);
- **boxes only** -- just draw detections on the live feed, never decide (use this to check a model);
- **Inspect** on a phone tile -- with several phones connected, choose which one is inspected (green outline);
- result banner OK / NOT OK / NOT FOUND, **Next car** (Space) to start again.

Rules (`mandatory`, `ignored`, `conf`, `scan_seconds`, ...) are in `runner_config.json` (see
`runner_config.example.json`). Optional `tasks.json` (see `tasks.example.json`) switches on VIN mode:
the VIN (QR/barcode in the inspected phone's view, typed in the viewer, or `POST /api/vin?vin=`) picks the
task = its own `.pt` + rules; results go to `results/results.csv` + images named with the VIN, then the
runner waits for the next VIN. Build with `build_exe.bat` (installs CPU-only torch to keep the exe small).


## Tasks (Pass / Fail / Ignore) -- PC Runner viewer
1. Pick the `.pt` in the top bar, click **+ Create task**, name it (e.g. Headlamp).
2. The pop-up lists the model's classes. In the **Pass**, **Fail** and **Ignore** dropdowns tick one or more
   classes (a class can be in only one list). Set **Scan time (seconds)** and **Min confidence**, **Save task**.
3. Saved to `inspection_tasks.json` next to the exe. **Edit task** re-opens it; saving overwrites by name; Delete removes it.
4. Choose the task (the dropdown shows only tasks of the selected `.pt`) and press **Start inspection**
   (**Stop** goes back to boxes-only).

Rules: OK = every Pass class seen and no Fail class; NOT OK = a Fail class (or an unassigned class when Pass is set)
on 2 frames in a row; Ignore never counts; after the scan time the last frame decides (NOT FOUND if nothing relevant).
No Pass classes = every class that is not Fail/Ignore must be seen. VIN mode (`tasks.json`) is unchanged and separate.

## Phone app: live window, torch, zoom
While streaming, the app shows **PC VIEW**: a ~3 fps snapshot of what the PC shows for this phone (`/preview.jpg?cam=me`,
boxes and result included; greys out when stale). Needs the PC to have been found by QR / Find PC. Torch button and a
1x / 1.5x / 2x / 3x zoom button drive the streaming camera (CameraX). Not yet compiled -- see "Not yet verified".
Next step: OK / NOT OK result banner + vibrate on the phone, and Region classes.
