"""Starts the Celery worker for EXE deployment."""

import datetime
import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path


def _celery_log_path() -> str:
    """Return an absolute path for the Celery worker log file.

    Celery's WatchedFileHandler crashes if --logfile is None, which happens
    in a frozen EXE where sys.stdout/stderr are redirected and Celery's log
    detection returns None instead of falling back to stdout.
    """
    if hasattr(sys, "_MEIPASS"):
        log_dir = Path(sys.executable).parent / "logs"
    else:
        log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return str(log_dir / "celery_worker.log")


class CeleryWorker:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None

    def start(self) -> None:
        print("[celery] Starting worker...")

        if hasattr(sys, "_MEIPASS"):
            # Running as PyInstaller EXE — sys.executable IS the EXE itself.
            # Spawning a subprocess would re-launch the full launcher in a loop.
            # Run the Celery worker inside a daemon thread instead.
            backend_path = str(Path(sys._MEIPASS) / "backend")
            if backend_path not in sys.path:
                sys.path.insert(0, backend_path)

            self._thread = threading.Thread(
                target=self._run_in_thread,
                daemon=True,
                name="celery-worker",
            )
            self._thread.start()
        else:
            # Development mode — safe to use subprocess with real Python.
            backend_path = str(Path(__file__).parent.parent.parent / "backend")
            env = {**os.environ, "PYTHONPATH": backend_path}
            self._process = subprocess.Popen(
                [
                    sys.executable, "-m", "celery",
                    "-A", "app.tasks.celery_app", "worker",
                    "--loglevel=info",
                    "--pool=solo",
                    "-Q", "celery",
                ],
                cwd=backend_path,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

        print("[celery] Worker started.")

    @staticmethod
    def _run_in_thread() -> None:
        try:
            from app.tasks.celery_app import celery_app
            celery_app.worker_main([
                "worker",
                "--loglevel=info",
                "--logfile", _celery_log_path(),
                "--pool=solo",
                "-Q", "celery",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
            ])
        except Exception:
            tb = traceback.format_exc()
            print(f"[celery] Worker thread crashed:\n{tb}", flush=True)
            try:
                crash_log = _celery_log_path().replace("celery_worker.log", "celery_crash.log")
                with open(crash_log, "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] CRASH:\n{tb}\n")
            except Exception:
                pass
        finally:
            print("[celery] Worker thread exiting.", flush=True)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        if self._process and self._process.poll() is None:
            print("[celery] Stopping worker...")
            self._process.terminate()
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._process.kill()
            print("[celery] Worker stopped.")
