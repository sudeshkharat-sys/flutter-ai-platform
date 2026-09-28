# connectivity_test_app -- step 1 throwaway test app

Purpose: prove a phone can stream camera frames to `pc_runner/runner.py`
over the local network. No detection, no UI polish, no relation to the
real generated app -- just camera -> JPEG -> WebSocket, on repeat, forever.
See `../../LIVE_FEED_RUNNER_PLAN.md` for why this exists and what comes
after it.

## Setup (this folder only ships `pubspec.yaml` + `lib/main.dart`;
Flutter's native android/ios scaffolding is generated fresh, not committed)

```bash
flutter create --project-name connectivity_test_app --org com.example .
```
Run that *inside* this `connectivity_test_app/` directory -- it fills in
`android/`, `ios/`, etc. without touching the `pubspec.yaml`/`lib/main.dart`
already here (confirm overwrite prompts choose "no" for those two files;
if it insists, just re-copy them back afterward).

Then add camera + internet permissions:

**android/app/src/main/AndroidManifest.xml** -- inside `<manifest>`, above
`<application>`:
```xml
<uses-permission android:name="android.permission.CAMERA"/>
<uses-permission android:name="android.permission.INTERNET"/>
```

**android/app/build.gradle** -- make sure `minSdkVersion` is at least 21
(the `camera` package requires it).

## Run

```bash
flutter pub get
flutter run
```

On launch it asks for the PC's IP (whatever `runner.py` printed, e.g.
`192.168.43.1`) and port (default `8090`), then streams the back camera to
`ws://<ip>:<port>/stream` continuously until you stop it. The screen shows
a live frame counter and connection status so you can see at a glance
whether it's actually sending.
