"""
Flutter AI Studio — Windows EXE Launcher
"""

from __future__ import annotations

import configparser
import logging
import multiprocessing
import os
import signal
import socket
import sys
import time
import traceback
import webbrowser
from pathlib import Path

# ---------------------------------------------------------------------------
if hasattr(sys, "_MEIPASS"):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

sys.path.insert(0, str(Path(__file__).parent))

# ---------------------------------------------------------------------------
# Logging setup — captures BOTH print() and logging.XXX() calls
# ---------------------------------------------------------------------------
_log_dir = BASE_DIR / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_log_path = _log_dir / "app.log"

# 1. File handle for the tee (captures print())
_log_file = open(_log_path, "w", buffering=1, encoding="utf-8")


class _Tee:
    """Mirror every write to two streams and flush immediately."""
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            try:
                s.write(data)
                s.flush()          # force-flush so nothing is lost on kill
            except Exception:
                pass

    def flush(self):
        for s in self._streams:
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self):
        return False


sys.stdout = _Tee(sys.__stdout__, _log_file)
sys.stderr = _Tee(sys.__stderr__, _log_file)

# 2. FileHandler for the Python logging module (captures uvicorn/celery logs)
_file_log_handler = logging.FileHandler(_log_path, mode="a", encoding="utf-8")
_file_log_handler.setLevel(logging.DEBUG)
_file_log_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
)
logging.root.addHandler(_file_log_handler)
logging.root.setLevel(logging.DEBUG)

print(f"[log] Writing logs to {_log_path}")

# ---------------------------------------------------------------------------
from services.postgres import PostgresManager
from services.redis_mgr import RedisManager
from services.celery_worker import CeleryWorker
import services.backend_svc as backend_svc

CFG_FILE = BASE_DIR / "flutterai.cfg"

DEFAULTS = {
    "postgres_port": "5432",
    "redis_port": "6379",
    "api_port": "8000",
    "db_name": "flutter_studio",
    "db_user": "flutter_user",
    "db_password": "flutter_local_pass",
    "open_browser": "true",
}


def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg["flutterai"] = DEFAULTS
    if CFG_FILE.exists():
        cfg.read(CFG_FILE)
    else:
        with open(CFG_FILE, "w") as f:
            cfg.write(f)
        print(f"[config] Created default config at {CFG_FILE}")
    return cfg


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def assert_port_free(port: int, service: str) -> None:
    if port_in_use(port):
        print(f"\n[ERROR] Port {port} is already in use — cannot start {service}.")
        print(f"        A previous FlutterAI process may still be running.")
        print(f"        Please close all FlutterAI windows, wait a few seconds,")
        print(f"        then try again. (Task Manager → find flutterai.exe → End Task)")
        print("\nPress Enter to close this window...")
        try:
            input()
        except Exception:
            time.sleep(5)
        sys.exit(1)


def wait_for_backend(port: int, timeout: int = 120) -> bool:
    import urllib.request
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(2)
    return False


def main() -> None:
    print("=" * 60)
    print("  Flutter AI Studio — Starting up")
    print("=" * 60)

    cfg = load_config()
    c = cfg["flutterai"]

    pg_port     = int(c["postgres_port"])
    redis_port  = int(c["redis_port"])
    api_port    = int(c["api_port"])
    db_name     = c["db_name"]
    db_user     = c["db_user"]
    db_password = c["db_password"]
    open_browser = c.getboolean("open_browser", fallback=True)

    print("\n[Step 1/4] Starting PostgreSQL...")
    assert_port_free(pg_port, "PostgreSQL")
    pg = PostgresManager(BASE_DIR, db_name, db_user, db_password, pg_port)
    if not pg.is_initialized():
        pg.initialize()
    pg.start()
    pg.create_db()

    print("\n[Step 2/4] Starting Redis...")
    assert_port_free(redis_port, "Redis")
    redis = RedisManager(BASE_DIR, redis_port)
    redis.start()

    print("\n[Step 3/4] Starting FastAPI backend...")
    assert_port_free(api_port, "Backend API")
    backend_svc.configure_env(BASE_DIR, db_user, db_password, db_name, pg_port, redis_port)
    uvicorn_thread = backend_svc.start(host="127.0.0.1", port=api_port)

    print("[backend] Waiting for health check...")
    if not wait_for_backend(api_port):
        print("[ERROR] Backend did not become healthy within 120 s.")
        print(f"[log]   Check {_log_path} for details.")
        _shutdown(pg, redis, None)
        print("\nPress Enter to close this window...")
        try:
            input()
        except Exception:
            pass
        sys.exit(1)
    print("[backend] Ready.")

    print("\n[Step 4/4] Starting Celery worker...")
    celery = CeleryWorker()
    celery.start()

    app_url = f"http://127.0.0.1:{api_port}"
    print("\n" + "=" * 60)
    print("  Flutter AI Studio is RUNNING")
    print(f"  Open your browser at: {app_url}")
    print(f"  Logs saved to: {_log_path}")
    print("  Press Ctrl+C to stop all services and exit.")
    print("=" * 60 + "\n")

    if open_browser:
        webbrowser.open(app_url)

    def _on_signal(sig, frame):
        print("\n[launcher] Shutdown signal received.")
        _shutdown(pg, redis, celery)
        _file_log_handler.flush()
        _file_log_handler.close()
        _log_file.flush()
        _log_file.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    while True:
        time.sleep(1)
        if not uvicorn_thread.is_alive():
            print("[ERROR] Uvicorn thread exited unexpectedly.")
            _shutdown(pg, redis, celery)
            sys.exit(1)


def _shutdown(pg, redis, celery) -> None:
    print("[launcher] Stopping services...")
    if celery:
        try:
            celery.stop()
        except Exception as e:
            print(f"[celery] Stop error: {e}")
    try:
        redis.stop()
    except Exception as e:
        print(f"[redis] Stop error: {e}")
    try:
        pg.stop()
    except Exception as e:
        print(f"[postgres] Stop error: {e}")
    print("[launcher] All services stopped.")


if __name__ == "__main__":
    # Required for Windows PyInstaller frozen executables that use multiprocessing.
    multiprocessing.freeze_support()

    try:
        main()
    except SystemExit:
        raise                 # let sys.exit() pass through normally
    except Exception as exc:
        print("\n" + "=" * 60)
        print("  [FATAL ERROR] Flutter AI Studio failed to start")
        print("=" * 60)
        print(f"\n  {exc}\n")
        traceback.print_exc()
        try:
            print(f"\n  Log file: {_log_path}")
        except Exception:
            pass
        print("\n  Press Enter to close this window...")
        try:
            input()
        except Exception:
            time.sleep(10)
        sys.exit(1)
