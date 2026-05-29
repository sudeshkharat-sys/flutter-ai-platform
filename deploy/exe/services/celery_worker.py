"""Starts the Celery worker for EXE deployment."""

import os
import subprocess
import sys
import threading
from pathlib import Path


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
        from app.tasks.celery_app import celery_app
        celery_app.worker_main([
            "worker",
            "--loglevel=info",
            "--pool=solo",
            "-Q", "celery",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
        ])

    def stop(self) -> None:
        if self._process and self._process.poll() is None:
            print("[celery] Stopping worker...")
            self._process.terminate()
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._process.kill()
            print("[celery] Worker stopped.")
