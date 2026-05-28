"""Starts the Celery worker in-process as a daemon thread for EXE deployment.

Subprocess approach doesn't work in PyInstaller because sys.executable
points to the bundled EXE, not a Python interpreter.
"""

import sys
import threading
from pathlib import Path


class CeleryWorker:
    def __init__(self):
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        print("[celery] Starting worker thread...")

        if hasattr(sys, "_MEIPASS"):
            backend_path = str(Path(sys._MEIPASS) / "backend")
        else:
            backend_path = str(Path(__file__).parent.parent.parent / "backend")

        if backend_path not in sys.path:
            sys.path.insert(0, backend_path)

        def run_worker():
            import os
            os.environ.setdefault("MPLBACKEND", "Agg")  # prevent matplotlib GUI in EXE
            from app.tasks.celery_app import celery_app
            argv = [
                "worker",
                "--loglevel=info",
                "--pool=solo",
                "-Q", "celery",
                "--without-gossip",
                "--without-mingle",
            ]
            celery_app.worker_main(argv=argv)

        self.thread = threading.Thread(target=run_worker, daemon=True, name="celery-worker")
        self.thread.start()
        print("[celery] Worker thread started.")

    def stop(self) -> None:
        print("[celery] Worker stopped (daemon thread exits with process).")
