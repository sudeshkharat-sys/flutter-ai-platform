# Flutter AI Studio — Windows EXE Build Setup Guide

This guide tells you exactly what to download and where to place each file
before running `build.bat`.

The goal: the final EXE runs on any Windows PC without any pre-installed tools.
All runtimes (Flutter, JDK, Android SDK, Gradle, PostgreSQL, Redis) are bundled
inside the `FlutterAI\` output folder.

---

## Folder layout expected before build

```
deploy\exe\
├── build.bat
├── launcher.py
├── launcher.spec
├── sdk_check.py
├── services\
└── resources\
    ├── postgres\          ← portable PostgreSQL 17
    ├── redis\             ← portable Redis 7 for Windows
    ├── flutter\           ← Flutter SDK (stable, Windows ZIP)
    ├── jdk\               ← Eclipse Temurin JDK 17 (portable ZIP)
    ├── android-sdk\       ← minimal Android SDK
    └── gradle\
        └── gradle-8.10.2-all.zip
```

---

## 1. PostgreSQL 17 (portable)

**Download:** https://www.enterprisedb.com/download-postgresql-binaries

1. Choose **Windows x86-64**, version **17.x**.
2. Download the ZIP (not the installer).
3. Extract so that the structure inside `resources\postgres\` looks like:

```
resources\postgres\
├── bin\
│   ├── pg_ctl.exe
│   ├── psql.exe
│   ├── initdb.exe
│   └── createdb.exe
├── lib\
└── share\
```

---

## 2. Redis 7 (portable, Windows)

**Download:** https://github.com/microsoftarchive/redis/releases

1. Download the latest **Redis-x64-\*.zip** (e.g. `Redis-x64-3.0.504.zip`).
2. Extract so that:

```
resources\redis\
└── redis-server.exe
```

> **Alternative:** Use the Memurai developer edition (Redis-compatible Windows port)
> from https://www.memurai.com/ — place `memurai.exe` renamed to `redis-server.exe`.

---

## 3. Flutter SDK (stable)

**Download:** https://docs.flutter.dev/release/archive (Windows section)

1. Download the latest **stable** Flutter Windows ZIP (e.g. `flutter_windows_3.x.x-stable.zip`).
2. Extract the **`flutter\`** folder from the ZIP directly into `resources\` so that:

```
resources\flutter\
├── bin\
│   ├── flutter.bat
│   └── dart.bat
├── packages\
└── ...
```

> **Size:** ~1.2 GB. This is the main contributor to build time and EXE size.

---

## 4. JDK 17 (Eclipse Temurin, portable ZIP)

**Download:** https://adoptium.net/temurin/releases/?version=17

1. Select: **Version 17**, **OS Windows**, **Architecture x64**, **Package JDK**, **Type zip**.
2. Download the ZIP (e.g. `OpenJDK17U-jdk_x64_windows_hotspot_17.x.x_x.zip`).
3. Extract the inner `jdk-17.x.x+x\` folder as `resources\jdk\`:

```
resources\jdk\
├── bin\
│   ├── java.exe
│   └── javac.exe
├── lib\
└── ...
```

---

## 5. Android SDK (minimal)

You need only the components required to build a Flutter APK.
**Total size:** ~1.5 GB.

### Option A — Copy from an existing Android Studio installation

If you have Android Studio installed:

1. Default SDK location: `C:\Users\<you>\AppData\Local\Android\Sdk`
2. Copy these sub-folders into `resources\android-sdk\`:

```
resources\android-sdk\
├── build-tools\
│   └── 34.0.0\          ← or whichever version Flutter requires
├── platforms\
│   └── android-34\
├── platform-tools\
│   └── adb.exe
└── cmdline-tools\
    └── latest\
        └── bin\
            ├── sdkmanager.bat
            └── avdmanager.bat
```

### Option B — Command-line SDK tools only

1. Download **Command line tools only** from https://developer.android.com/studio#command-line-tools-only  
   (e.g. `commandlinetools-win-11076708_latest.zip`)
2. Extract to `resources\android-sdk\cmdline-tools\latest\`
3. Run:
   ```bat
   resources\android-sdk\cmdline-tools\latest\bin\sdkmanager.bat ^
       "build-tools;34.0.0" "platforms;android-34" "platform-tools"
   ```
   Accept licences when prompted.

---

## 6. Gradle 8.10.2 distribution ZIP

**Download:** https://services.gradle.org/distributions/gradle-8.10.2-all.zip

Place the file at:

```
resources\gradle\
└── gradle-8.10.2-all.zip
```

Do **not** extract it — the launcher unpacks it on first APK build via the
Gradle wrapper.

---

## Build steps

Once all six resources are in place:

```bat
cd deploy\exe
build.bat
```

The script will:
1. Check Python 3.11+ and Node.js 18+
2. Build the React frontend (`frontend\`)
3. Install Python dependencies (PyInstaller + all backend deps)
4. Verify that the six resources above are present
5. Run PyInstaller — output lands at `D:\FlutterAI-App\FlutterAI\`

---

## Running on the target PC

Copy the entire `D:\FlutterAI-App\FlutterAI\` folder to the target machine.
No other software needs to be installed.

```bat
cd FlutterAI
flutterai.exe
```

On first launch the app:
- Verifies bundled build tools (shows a report)
- Initialises the PostgreSQL database
- Starts PostgreSQL, Redis, FastAPI backend, and Celery worker
- Opens your browser at http://localhost:8001

### Configuration

A `flutterai.cfg` file is created next to the EXE on first run.
You can edit it to change ports or disable the auto-browser-open:

```ini
[flutterai]
postgres_port  = 5433
redis_port     = 6380
api_port       = 8001
db_name        = flutter_studio
db_user        = flutterai
db_password    = flutterai_local_pass
open_browser   = true
skip_sdk_check = false
```

### Port conflicts

If any of the three ports (5433, 6380, 8001) are already in use the launcher
will print an error and exit. Change the port in `flutterai.cfg` and restart.

---

## Disk space summary

| Resource         | Approx. size |
|------------------|--------------|
| Flutter SDK      | ~1.2 GB      |
| JDK 17           | ~300 MB      |
| Android SDK      | ~1.5 GB      |
| Gradle ZIP       | ~130 MB      |
| PostgreSQL       | ~50 MB       |
| Redis            | ~5 MB        |
| Python + backend | ~500 MB      |
| React frontend   | ~10 MB       |
| **Total**        | **~3.7 GB**  |
