# FlutterAIStudio — Standalone Windows EXE Build

## What this produces
A single `FlutterAIStudio_Setup.exe` installer (~4-5 GB) that bundles:
- Python backend (FastAPI + Celery + all ML libs)
- React frontend (served by the backend)
- Redis (portable)
- PostgreSQL 15 (portable)
- Flutter SDK (Windows)
- JDK 17 (Eclipse Temurin portable)

No installation of Python, Node, Flutter, Java, Redis, or PostgreSQL required on the target machine.

## Build machine requirements (Windows only)
| Requirement | Version |
|---|---|
| Windows | 10 or 11 (64-bit) |
| Python | 3.11+ (must be on PATH) |
| Node.js | 18+ (must be on PATH) |
| Inno Setup 6 | Install from https://jrsoftware.org/isinfo.php |
| Disk space | ~15 GB free (deps + build artifacts) |
| RAM | 8 GB+ recommended |

## Build steps

### Step 1 — Download external dependencies (one time only)
Open PowerShell as Administrator and run:
```powershell
cd exe-build
powershell -ExecutionPolicy Bypass -File download_deps.ps1
```
This downloads Flutter SDK, JDK 17, Redis, and PostgreSQL into `exe-build\deps\`.
Takes ~10-20 minutes depending on internet speed.

### Step 2 — Build the EXE
```bat
cd exe-build
build_windows.bat
```
This will:
1. Build the React frontend (`npm run build`)
2. Install Python deps + PyInstaller
3. Run PyInstaller (takes 5-15 minutes)
4. Assemble all components into a staging folder
5. Create `dist\FlutterAIStudio_Setup.exe` with Inno Setup

### Step 3 — Distribute
Share `exe-build\dist\FlutterAIStudio_Setup.exe` with end users.

## How the installed app works
```
User double-clicks FlutterAIStudio.exe
  ↓
Launcher starts silently:
  - Redis        (localhost:6379)
  - PostgreSQL   (localhost:5432)  ← auto-initialized on first run
  - FastAPI      (localhost:8000)  ← also serves the React UI
  - Celery worker (background tasks)
  ↓
Browser opens automatically → http://localhost:8000
  ↓
Press Ctrl+C or close window → all services stop cleanly
```

## File structure after install
```
C:\Program Files\FlutterAIStudio\
├── FlutterAIStudio.exe       ← Launch this
├── _internal\                ← PyInstaller Python bundle (do not touch)
├── bin\
│   ├── redis\                ← Portable Redis
│   └── postgresql\           ← Portable PostgreSQL
├── flutter\                  ← Flutter SDK
├── jdk\                      ← Java JDK 17
├── frontend_build\           ← React static files
└── data\                     ← User data (models, exports, images, DB)
    ├── models\
    ├── exports\
    ├── reference_images\
    └── pgdata\               ← PostgreSQL data (auto-created on first run)
```

## Logs
If something fails to start, check these files inside the `data\` folder:
- `backend.log` — FastAPI server log
- `celery.log` — Celery worker log
- `postgres.log` — PostgreSQL log

## Troubleshooting
| Problem | Fix |
|---|---|
| Port 6379 already in use | Another Redis is running — stop it or change port in `bin\redis\redis.conf` |
| Port 5432 already in use | Another PostgreSQL is running — stop it |
| Port 8000 already in use | Another app uses port 8000 — close it |
| APK build fails | Check that Flutter and Java paths resolve correctly in the launcher |
| Browser doesn't open | Navigate manually to http://localhost:8000 |
