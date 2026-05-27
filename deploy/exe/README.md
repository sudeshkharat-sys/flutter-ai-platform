# Flutter AI Studio — Windows EXE Deployment

Self-contained Windows executable that bundles the full Flutter AI Studio stack
(PostgreSQL + Redis + FastAPI backend + React frontend) into a single folder.
No Docker, no Python install, no network access required on the target machine.

**CPU-only build** — safe for corporate laptops with Zscaler / network inspection.

---

## What you need before building

| Requirement | Where to get it |
|---|---|
| Python 3.11 (build machine) | https://www.python.org/downloads/ |
| Node.js 18+ (build machine) | https://nodejs.org/ |
| Portable PostgreSQL 17 binaries | https://www.enterprisedb.com/download-postgresql-binaries |
| Portable Redis 7 for Windows | https://github.com/microsoftarchive/redis/releases |

### Portable binary layout

After downloading, place files in these exact paths:

```
deploy/exe/resources/
  postgres/
    bin/         <- pg_ctl.exe, initdb.exe, psql.exe, createdb.exe, ...
    lib/
    share/
  redis/
    redis-server.exe
    redis-cli.exe
```

---

## Build

```bat
cd deploy\exe
build.bat
```

Output: `D:\FlutterAI-App\FlutterAI\flutterai.exe`

> **Zscaler note:** Run `build.bat` on a machine that can reach PyPI and
> PyTorch's download server.  The output folder is fully self-contained
> and works on any Windows 10/11 machine, even those behind Zscaler.

---

## Run

```bat
cd D:\FlutterAI-App\FlutterAI
flutterai.exe
```

First launch:
1. Initializes the PostgreSQL `flutter_studio` database
2. Starts Redis
3. Starts the FastAPI backend (with React frontend bundled)
4. Starts the Celery worker
5. Opens `http://localhost:8000` in your browser

Press **Ctrl+C** in the console to stop all services cleanly.

---

## Configuration

A `flutterai.cfg` file is created next to the exe on first run:

```ini
[flutterai]
postgres_port = 5432
redis_port    = 6379
api_port      = 8000
db_name       = flutter_studio
db_user       = flutter_user
db_password   = flutter_local_pass
open_browser  = true
```

Edit this file to change ports or credentials before launching.

---

## Data persistence

All runtime data lives in a `data/` folder **next to the exe** (not inside it),
so reinstalling / updating the app does not wipe the database or uploaded files.

```
FlutterAI/
  flutterai.exe
  flutterai.cfg       <- edit to change ports/credentials
  data/
    pgdata/           <- PostgreSQL cluster (preserved across updates)
    redis/            <- Redis AOF persistence
    models/           <- uploaded .pt model files
    exports/          <- exported APK/TFLite artefacts
    reference_images/
  logs/
    postgres.log
    redis.log
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Port 5432 already in use` | Another PostgreSQL is running. Change `postgres_port` in `flutterai.cfg` or stop the other instance. |
| `Port 6379 already in use` | Another Redis is running. Change `redis_port` in `flutterai.cfg`. |
| `Backend did not become healthy` | Check `logs/` folder for Python tracebacks. |
| SSL/cert errors during build | See Zscaler note above. |
| Slow model inference | Expected — CPU-only mode. Inference still works, just slower than GPU. |
