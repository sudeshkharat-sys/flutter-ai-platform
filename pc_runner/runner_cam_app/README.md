# Runner Cam (phone app)

Turns the phone camera into a CCTV-style feed for `pc_runner/runner.exe`
(like IP Webcam). Back/front camera, quality + frame-rate controls,
auto-reconnect, screen kept awake, and a **debug log** panel (Copy button;
log lines are also forwarded to the PC's log).

## Build

Easiest: GitHub Actions -> "Runner Cam build (exe + apk)" -> download the
`runner-cam-apk` and `runner-exe` artifacts. The workflow runs
`flutter create` and patches the manifest (CAMERA, INTERNET and
`usesCleartextTraffic="true"` -- required for `ws://` on Android 9+) so the
native scaffolding is never committed.

Manual build (this folder only ships `pubspec.yaml` + `lib/`):

```bash
flutter create --project-name runner_cam --org com.example --platforms android .
# then add to android/app/src/main/AndroidManifest.xml:
#   <uses-permission android:name="android.permission.CAMERA"/>
#   <uses-permission android:name="android.permission.INTERNET"/>
#   <application android:usesCleartextTraffic="true" android:label="Runner Cam" ...>
flutter pub get && flutter build apk --release
```

## Use

1. Run `runner.exe` on the PC (same Wi-Fi, or the PC joined to the phone's hotspot).
2. Open Runner Cam -> it scans automatically (UDP beacon + sweep of the
   subnet). Tap the PC that appears, or type the IP:port the exe printed.
3. Start streaming. The PC browser viewer (opens automatically) shows the feed.

If something fails, tap the bug icon / read the DEBUG LOG: it says whether the
camera, the network scan, the connect (timeout = firewall / wrong Wi-Fi) or the
frame upload failed.

## VIN -> task -> result (tasks.json)

Put `tasks.json` next to `runner.exe` (see `tasks.example.json`). Each task has `vins`
(patterns, first match wins, `*` = any), its own `model` (.pt) and optional overrides
(`mandatory`, `ignored`, `conf`, `scan_seconds`, ...). With this file the runner idles on
"SCAN VIN"; show the car's VIN QR/barcode to the phone camera (or type the VIN in the viewer,
or `POST /api/vin?vin=...`). It loads that task's model, scans, shows OK / NOT OK / NOT FOUND
on the PC and phone (vibration), saves `results/results.csv` + images named with the VIN,
then returns to "SCAN VIN" after `next_after_seconds` (default 4). The same VIN is ignored for
`rescan_block_seconds` (default 15). Without `tasks.json` the old single-model mode applies.
