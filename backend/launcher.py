"""
Flutter AI Studio — portable launcher.

Starts bundled PostgreSQL, Redis, Celery worker, and the FastAPI server.
Double-click FlutterAIStudio.exe — everything starts automatically.
Pass --celery-worker to run as the Celery worker process (called internally).
"""

import os
import sys
import time
import socket
import subprocess
import threading
import webbrowser
import signal
import atexit
import shutil
from pathlib import Path


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_app_base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


def _wait_for_port(host: str, port: int, timeout: float = 60.0, label: str = "") -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    print(f"  [WARN] Timed out waiting for {label or f'{host}:{port}'}")
    return False


def _log(msg: str):
    print(msg, flush=True)


# ── Environment setup ──────────────────────────────────────────────────────────

def _setup_env(app_base: Path):
    """Set PATH and env vars pointing at bundled tools."""
    flutter_root = app_base / "flutter"
    java_home    = app_base / "jdk-17"
    android_home = app_base / "android-sdk"

    if java_home.exists():
        os.environ["JAVA_HOME"] = str(java_home)
    if android_home.exists():
        os.environ["ANDROID_HOME"] = str(android_home)
    if flutter_root.exists():
        os.environ["FLUTTER_ROOT"] = str(flutter_root)

    extra = []
    for p in [
        java_home / "bin",
        flutter_root / "bin",
        android_home / "cmdline-tools" / "latest" / "bin",
        android_home / "platform-tools",
    ]:
        if p.exists():
            extra.append(str(p))

    if extra:
        os.environ["PATH"] = os.pathsep.join(extra) + os.pathsep + os.environ.get("PATH", "")

    data_dir = app_base / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(str(app_base))

    if not getattr(sys, "frozen", False):
        backend_dir = Path(__file__).parent
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))


# ── PostgreSQL ─────────────────────────────────────────────────────────────────

def _start_postgres(app_base: Path) -> subprocess.Popen | None:
    pg_bin  = app_base / "postgresql" / "bin"
    pg_ctl  = pg_bin / "pg_ctl.exe"
    initdb  = pg_bin / "initdb.exe"
    createdb = pg_bin / "createdb.exe"

    if not pg_ctl.exists():
        _log("  [WARN] Bundled PostgreSQL not found — make sure postgresql/ is in the package")
        return None

    data_dir = app_base / "data" / "pgdata"
    log_file = app_base / "data" / "postgres.log"
    db_name  = "flutter_studio"

    # ── First-time init ──────────────────────────────────────────────────────
    if not data_dir.exists():
        _log("  Initializing PostgreSQL data directory (first run)…")
        data_dir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                str(initdb),
                "-D", str(data_dir),
                "--username=postgres",
                "-A", "trust",          # no password needed — local only
                "--encoding=UTF8",
                "--locale=C",
            ],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            _log(f"  [ERROR] initdb failed:\n{result.stderr}")
            return None
        _log("  PostgreSQL initialized.")

    # ── Start server ─────────────────────────────────────────────────────────
    _log("  Starting PostgreSQL…")
    subprocess.run(
        [str(pg_ctl), "start", "-D", str(data_dir), "-l", str(log_file), "-w"],
        capture_output=True
    )

    if not _wait_for_port("127.0.0.1", 5432, timeout=30, label="PostgreSQL"):
        _log("  [ERROR] PostgreSQL did not start in time.")
        return None

    _log("  PostgreSQL ready on port 5432")

    # ── Create app database if needed ────────────────────────────────────────
    result = subprocess.run(
        [str(createdb), "-h", "localhost", "-U", "postgres", db_name],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        _log(f"  Created database '{db_name}'")
    # returncode != 0 just means it already exists — that's fine

    # Set env vars so the FastAPI app and Celery can connect
    os.environ["POSTGRES_USER"]     = "postgres"
    os.environ["POSTGRES_PASSWORD"] = ""
    os.environ["POSTGRES_HOST"]     = "localhost"
    os.environ["POSTGRES_PORT"]     = "5432"
    os.environ["POSTGRES_DB"]       = db_name

    return True   # pg_ctl manages the server process, not us


def _stop_postgres(app_base: Path):
    pg_ctl   = app_base / "postgresql" / "bin" / "pg_ctl.exe"
    data_dir = app_base / "data" / "pgdata"
    if pg_ctl.exists() and data_dir.exists():
        _log("  Stopping PostgreSQL…")
        subprocess.run(
            [str(pg_ctl), "stop", "-D", str(data_dir), "-m", "fast"],
            capture_output=True
        )


# ── Redis ──────────────────────────────────────────────────────────────────────

def _start_redis(app_base: Path) -> subprocess.Popen | None:
    redis_exe = app_base / "redis" / "redis-server.exe"
    if not redis_exe.exists():
        _log("  [WARN] Bundled Redis not found — make sure redis/ is in the package")
        return None

    _log("  Starting Redis…")
    proc = subprocess.Popen(
        [str(redis_exe), "--port", "6379", "--loglevel", "warning"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not _wait_for_port("127.0.0.1", 6379, timeout=15, label="Redis"):
        _log("  [ERROR] Redis did not start in time.")
        proc.kill()
        return None

    _log("  Redis ready on port 6379")

    os.environ["REDIS_URL"]            = "redis://localhost:6379/0"
    os.environ["CELERY_BROKER_URL"]    = "redis://localhost:6379/3"
    os.environ["CELERY_RESULT_BACKEND"] = "redis://localhost:6379/4"

    return proc


# ── Celery Worker ──────────────────────────────────────────────────────────────

def _start_celery_worker(app_base: Path) -> subprocess.Popen:
    """
    Launch a Celery worker as a subprocess of this same exe.
    The worker receives --celery-worker and goes to run_celery_worker().
    """
    _log("  Starting Celery worker…")

    env = os.environ.copy()
    proc = subprocess.Popen(
        [sys.executable, "--celery-worker"],
        cwd=str(app_base),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    # Stream Celery output in background thread
    def _pipe():
        for line in proc.stdout:
            print(f"  [celery] {line}", end="", flush=True)

    threading.Thread(target=_pipe, daemon=True).start()
    time.sleep(2)
    _log("  Celery worker started")
    return proc


def run_celery_worker():
    """Entry point when launched with --celery-worker flag."""
    app_base = _get_app_base()
    _setup_env(app_base)

    # Reload settings now that env vars are set
    import importlib
    import app.config as _cfg
    _cfg.get_settings.cache_clear()
    importlib.reload(_cfg)

    from celery.__main__ import main as celery_main
    sys.argv = [
        "celery",
        "-A", "app.tasks.celery_app",
        "worker",
        "--loglevel=info",
        "--pool=solo",
    ]
    celery_main()


# ── Main launcher ──────────────────────────────────────────────────────────────

def main():
    # ── Celery worker mode ───────────────────────────────────────────────────
    if "--celery-worker" in sys.argv:
        run_celery_worker()
        return

    app_base = _get_app_base()
    _setup_env(app_base)

    port = int(os.environ.get("PORT", 8001))
    url  = f"http://localhost:{port}"

    _log("=" * 55)
    _log("  Flutter AI Studio")
    _log("=" * 55)

    # ── Start services ───────────────────────────────────────────────────────
    _log("\n[1/4] PostgreSQL")
    pg_ok = _start_postgres(app_base)

    _log("\n[2/4] Redis")
    redis_proc = _start_redis(app_base)

    _log("\n[3/4] Celery worker")
    celery_proc = _start_celery_worker(app_base)

    # ── Graceful shutdown ────────────────────────────────────────────────────
    procs_to_kill = [p for p in [redis_proc, celery_proc] if p]

    def _shutdown(*_):
        _log("\nShutting down…")
        for p in procs_to_kill:
            try:
                p.terminate()
            except Exception:
                pass
        _stop_postgres(app_base)
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    atexit.register(_shutdown)

    # ── Open browser ─────────────────────────────────────────────────────────
    def _open():
        time.sleep(4)
        webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()

    # ── Start FastAPI ─────────────────────────────────────────────────────────
    _log(f"\n[4/4] FastAPI server")
    _log(f"\n{'=' * 55}")
    _log(f"  App running at  {url}")
    _log(f"  Press Ctrl+C to stop all services")
    _log(f"{'=' * 55}\n")

    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
