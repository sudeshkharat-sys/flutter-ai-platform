"""Manages embedded portable PostgreSQL for the EXE deployment."""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


class PostgresManager:
    def __init__(self, bundle_dir: Path, data_dir: Path, db_name: str, db_user: str, db_password: str, port: int):
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password
        self.port = port

        self.pg_bin  = bundle_dir / "postgres" / "bin"
        self.pg_data = data_dir   / "data"     / "pgdata"
        self.pg_log  = data_dir   / "logs"     / "postgres.log"
        self._process = None

    def _bin(self, name: str) -> str:
        return str(self.pg_bin / name)

    def is_initialized(self) -> bool:
        return (self.pg_data / "PG_VERSION").exists()

    def initialize(self) -> None:
        print(f"[postgres] Initializing database cluster at {self.pg_data} ...")
        sys.stdout.flush()
        # Remove any partial init from a previous failed run
        if self.pg_data.exists() and not self.is_initialized():
            shutil.rmtree(self.pg_data)
        self.pg_data.mkdir(parents=True, exist_ok=True)
        self.pg_log.parent.mkdir(parents=True, exist_ok=True)

        # --pwfile=- (stdin) is not supported on Windows; write to a temp file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as pwf:
            pwf.write(self.db_password + "\n")
            pwfile_path = pwf.name

        try:
            result = subprocess.run(
                [
                    self._bin("initdb.exe"),
                    "-D", str(self.pg_data),
                    "-U", self.db_user,
                    "--encoding=UTF8",
                    "--auth=md5",
                    f"--pwfile={pwfile_path}",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"initdb failed:\n{result.stderr}")
            print("[postgres] Cluster initialized.")
            sys.stdout.flush()
        finally:
            os.unlink(pwfile_path)

    def start(self) -> None:
        print(f"[postgres] Starting on port {self.port} ...")
        sys.stdout.flush()
        self.pg_log.parent.mkdir(parents=True, exist_ok=True)

        # Start postgres.exe directly with DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP
        # so it is fully isolated from the launcher console and won't receive
        # Ctrl+C signals that kill the background worker processes (0xC000013A).
        pg_exe = self.pg_bin / "postgres.exe"
        log_fh = open(str(self.pg_log), "a")
        self._process = subprocess.Popen(
            [str(pg_exe), "-D", str(self.pg_data), "-p", str(self.port)],
            stdout=log_fh,
            stderr=log_fh,
            creationflags=(
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            ) if sys.platform == "win32" else 0,
        )

        # Wait until the port accepts connections (up to 120 s)
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    print("[postgres] Started.")
                    sys.stdout.flush()
                    return
            except OSError:
                time.sleep(1)

        raise RuntimeError(
            f"PostgreSQL did not start on port {self.port} within 120 s. "
            f"Check log: {self.pg_log}"
        )

    def create_db(self) -> None:
        env_pg = {
            "PGPASSWORD": self.db_password,
            "PATH": str(self.pg_bin),
        }
        result = subprocess.run(
            [
                self._bin("psql.exe"),
                "-U", self.db_user,
                "-p", str(self.port),
                "-d", "postgres",
                "-tc", f"SELECT 1 FROM pg_database WHERE datname='{self.db_name}'",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, **env_pg},
        )
        if "1" not in result.stdout:
            print(f"[postgres] Creating database '{self.db_name}' ...")
            subprocess.run(
                [
                    self._bin("createdb.exe"),
                    "-U", self.db_user,
                    "-p", str(self.port),
                    self.db_name,
                ],
                env={**os.environ, **env_pg},
                check=True,
            )
            print(f"[postgres] Database '{self.db_name}' created.")
        else:
            print(f"[postgres] Database '{self.db_name}' already exists.")
        sys.stdout.flush()

    def stop(self) -> None:
        print("[postgres] Stopping ...")
        if self._process and self._process.poll() is None:
            subprocess.run(
                [self._bin("pg_ctl.exe"), "stop", "-D", str(self.pg_data), "-m", "fast"],
                capture_output=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            )
        print("[postgres] Stopped.")
