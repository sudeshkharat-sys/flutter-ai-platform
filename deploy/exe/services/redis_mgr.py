"""Manages embedded portable Redis for the EXE deployment."""

import subprocess
import sys
import time
from pathlib import Path


class RedisManager:
    def __init__(self, bundle_dir: Path, data_dir: Path, port: int):
        self.port = port
        self.redis_exe = bundle_dir / "redis" / "redis-server.exe"  # bundled binary
        self.redis_dir = data_dir   / "data"  / "redis"             # writable data
        self.redis_log = data_dir   / "logs"  / "redis.log"
        self._process = None

    def start(self) -> None:
        print(f"[redis] Starting on port {self.port} ...")
        self.redis_dir.mkdir(parents=True, exist_ok=True)
        self.redis_log.parent.mkdir(parents=True, exist_ok=True)

        self._process = subprocess.Popen(
            [
                str(self.redis_exe),
                "--port", str(self.port),
                "--appendonly", "yes",
                "--dir", str(self.redis_dir),
                "--logfile", str(self.redis_log),
            ],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        # Wait for Redis to accept connections
        import socket
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    break
            except OSError:
                time.sleep(0.5)
        else:
            raise RuntimeError(f"Redis did not start on port {self.port} within 15 s.")
        print("[redis] Started.")

    def stop(self) -> None:
        if self._process and self._process.poll() is None:
            print("[redis] Stopping ...")
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
            print("[redis] Stopped.")
