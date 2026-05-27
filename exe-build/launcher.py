"""
FlutterAIStudio Launcher
Starts Redis, PostgreSQL, FastAPI backend, Celery worker, then opens browser.
This script is packaged by PyInstaller into FlutterAIStudio.exe
"""

import sys
import os
import multiprocessing

# Required for PyInstaller + Celery (Windows spawns fresh processes)
multiprocessing.freeze_support()

# If invoked as celery worker subprocess
if len(sys.argv) > 1 and sys.argv[1] == "--celery-worker":
    # Ensure the bundled app package is importable
    if getattr(sys, "frozen", False):
        bundle_dir = sys._MEIPASS
        sys.path.insert(0, bundle_dir)
    from app.tasks.celery_app import celery_app
    import app.tasks.convert_model  # noqa: F401
    import app.tasks.build_apk      # noqa: F401
    celery_app.worker_main(["worker", "--loglevel=info", "--concurrency=2", "-P", "solo"])
    sys.exit(0)

# ── Normal launcher mode ──────────────────────────────────────────────────────

import subprocess
import threading
import time
import webbrowser
import signal
import ctypes
import tempfile
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

def get_install_dir() -> Path:
    """Root of the installation directory (where the EXE lives)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent

BASE = get_install_dir()
BIN       = BASE / "bin"
REDIS_EXE = BIN  / "redis"    / "redis-server.exe"
PG_BIN    = BIN  / "postgresql" / "bin"
PG_DATA   = BASE / "data" / "pgdata"
FLUTTER   = BASE / "flutter"
JDK       = BASE / "jdk"
DATA_DIR  = BASE / "data"
MODELS_DIR     = DATA_DIR / "models"
EXPORTS_DIR    = DATA_DIR / "exports"
REF_IMAGES_DIR = DATA_DIR / "reference_images"
FRONTEND_BUILD = BASE / "frontend_build"

PG_PORT   = 5432
API_PORT  = 8000
PG_USER   = "postgres"
PG_PASS   = "flutterstudio"
PG_DB     = "flutter_studio"

_procs: list[subprocess.Popen] = []


# ── Environment ───────────────────────────────────────────────────────────────

def build_env() -> dict:
    env = os.environ.copy()
    # Prepend bundled tools to PATH
    extra = ";".join([
        str(FLUTTER / "bin"),
        str(JDK / "bin"),
        str(BIN / "redis"),
    ])
    env["PATH"] = extra + ";" + env.get("PATH", "")
    env["JAVA_HOME"]  = str(JDK)
    env["FLUTTER_ROOT"] = str(FLUTTER)
    env["PUB_CACHE"]  = str(BASE / "flutter_pub_cache")
    # Backend settings
    env["POSTGRES_HOST"]     = "localhost"
    env["POSTGRES_PORT"]     = str(PG_PORT)
    env["POSTGRES_USER"]     = PG_USER
    env["POSTGRES_PASSWORD"] = PG_PASS
    env["POSTGRES_DB"]       = PG_DB
    env["REDIS_URL"]         = "redis://localhost:6379/0"
    env["CELERY_BROKER_URL"] = "redis://localhost:6379/3"
    env["CELERY_RESULT_BACKEND"] = "redis://localhost:6379/4"
    env["MODELS_DIR"]        = str(MODELS_DIR)
    env["EXPORTS_DIR"]       = str(EXPORTS_DIR)
    env["REFERENCE_IMAGES_DIR"] = str(REF_IMAGES_DIR)
    return env


# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def wait_tcp(host: str, port: int, timeout: int = 60) -> bool:
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def spawn(args, env=None, **kwargs) -> subprocess.Popen:
    # Hide console window for child processes
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    proc = subprocess.Popen(
        args,
        env=env,
        startupinfo=si,
        creationflags=subprocess.CREATE_NO_WINDOW,
        **kwargs,
    )
    _procs.append(proc)
    return proc


# ── Service starters ──────────────────────────────────────────────────────────

def start_redis():
    log("Starting Redis...")
    redis_conf = BIN / "redis" / "redis.conf"
    args = [str(REDIS_EXE)]
    if redis_conf.exists():
        args.append(str(redis_conf))
    proc = spawn(args)
    if not wait_tcp("localhost", 6379, timeout=15):
        raise RuntimeError("Redis failed to start within 15s")
    log("Redis ready.")
    return proc


def init_postgres():
    """Initialize PostgreSQL data directory on first run."""
    if PG_DATA.exists() and (PG_DATA / "PG_VERSION").exists():
        return
    log("Initializing PostgreSQL database (first run)...")
    PG_DATA.mkdir(parents=True, exist_ok=True)

    # Write password file
    pwfile = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    pwfile.write(PG_PASS)
    pwfile.close()

    result = subprocess.run(
        [
            str(PG_BIN / "initdb.exe"),
            "-D", str(PG_DATA),
            "-U", PG_USER,
            f"--pwfile={pwfile.name}",
            "-E", "UTF8",
            "--locale=C",
        ],
        capture_output=True, text=True
    )
    os.unlink(pwfile.name)
    if result.returncode != 0:
        raise RuntimeError(f"initdb failed:\n{result.stderr}")
    log("PostgreSQL initialized.")


def start_postgres():
    init_postgres()
    log("Starting PostgreSQL...")
    proc = spawn(
        [
            str(PG_BIN / "pg_ctl.exe"),
            "start", "-D", str(PG_DATA),
            "-l", str(DATA_DIR / "postgres.log"),
            "-o", f"-p {PG_PORT}",
        ]
    )
    if not wait_tcp("localhost", PG_PORT, timeout=30):
        raise RuntimeError("PostgreSQL failed to start within 30s")
    # Create application database if it doesn't exist
    subprocess.run(
        [
            str(PG_BIN / "createdb.exe"),
            "-h", "localhost", "-p", str(PG_PORT),
            "-U", PG_USER, PG_DB,
        ],
        capture_output=True,
        env={**os.environ, "PGPASSWORD": PG_PASS},
    )
    log("PostgreSQL ready.")
    return proc


def start_backend(env: dict):
    log("Starting backend API server...")
    exe = sys.executable if getattr(sys, "frozen", False) else sys.executable
    proc = spawn(
        [exe, "--api-server"],
        env=env,
        stdout=open(DATA_DIR / "backend.log", "a"),
        stderr=subprocess.STDOUT,
    )
    if not wait_tcp("localhost", API_PORT, timeout=60):
        raise RuntimeError("Backend API failed to start within 60s")
    log("Backend API ready.")
    return proc


def start_celery(env: dict):
    log("Starting Celery worker...")
    exe = sys.executable if getattr(sys, "frozen", False) else sys.executable
    proc = spawn(
        [exe, "--celery-worker"],
        env=env,
        stdout=open(DATA_DIR / "celery.log", "a"),
        stderr=subprocess.STDOUT,
    )
    log("Celery worker started.")
    return proc


# ── API server entry (used when called with --api-server flag) ────────────────

def run_api_server():
    if getattr(sys, "frozen", False):
        sys.path.insert(0, sys._MEIPASS)
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=API_PORT,
        log_level="info",
    )


# ── Shutdown ──────────────────────────────────────────────────────────────────

def shutdown():
    log("Shutting down services...")
    for proc in reversed(_procs):
        try:
            proc.terminate()
        except Exception:
            pass
    # Stop PostgreSQL gracefully
    subprocess.run(
        [str(PG_BIN / "pg_ctl.exe"), "stop", "-D", str(PG_DATA), "-m", "fast"],
        capture_output=True,
    )
    log("All services stopped. Goodbye.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--api-server":
        run_api_server()
        return

    # Ensure data dirs exist
    for d in [MODELS_DIR, EXPORTS_DIR, REF_IMAGES_DIR, DATA_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    print("=" * 55)
    print("   FlutterAIStudio - Starting up...")
    print("=" * 55)

    try:
        env = build_env()
        start_redis()
        start_postgres()
        start_backend(env)
        start_celery(env)

        url = f"http://localhost:{API_PORT}"
        log(f"Opening {url}")
        webbrowser.open(url)

        print("\n" + "=" * 55)
        print(f"  App running at {url}")
        print("  Press Ctrl+C to stop all services.")
        print("=" * 55 + "\n")

        # Keep alive
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        pass
    except Exception as e:
        log(f"ERROR: {e}")
        input("Press Enter to exit...")
    finally:
        shutdown()


if __name__ == "__main__":
    main()
