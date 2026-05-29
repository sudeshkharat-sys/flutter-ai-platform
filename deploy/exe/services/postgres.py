"""Manages embedded portable PostgreSQL for the EXE deployment."""

import os
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

        self.pg_bin  = bundle_dir / "postgres" / "bin"  # read-only bundled binaries
        self.pg_data = data_dir   / "data"     / "pgdata"  # writable user data
        self.pg_log  = data_dir   / "logs"     / "postgres.log"
        self._process = None

    def _bin(self, name: str) -> str:
        return str(self.pg_bin / name)

    def is_initialized(self) -> bool:
        return (self.pg_data / "PG_VERSION").exists()

    def initialize(self) -> None:
        print(f"[postgres] Initializing database cluster at {self.pg_data} ...")
        self.pg_data.mkdir(parents=True, exist_ok=True)
        self.pg_log.parent.mkdir(parents=True, exist_ok=True)

        # --pwfile=- (stdin) doesn't work on Windows; use a temp file instead
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
        finally:
            os.unlink(pwfile_path)

    def start(self) -> None:
        print(f"[postgres] Starting on port {self.port} ...")
        self.pg_log.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                self._bin("pg_ctl.exe"),
                "start",
                "-D", str(self.pg_data),
                "-l", str(self.pg_log),
                "-o", f"-p {self.port}",
                "-w",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"pg_ctl start failed:\n{result.stderr}\n{result.stdout}")
        print("[postgres] Started.")

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
            env={**__import__("os").environ, **env_pg},
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
                env={**__import__("os").environ, **env_pg},
                check=True,
            )
            print(f"[postgres] Database '{self.db_name}' created.")
        else:
            print(f"[postgres] Database '{self.db_name}' already exists.")

    def stop(self) -> None:
        print("[postgres] Stopping ...")
        subprocess.run(
            [self._bin("pg_ctl.exe"), "stop", "-D", str(self.pg_data), "-m", "fast"],
            capture_output=True,
        )
        print("[postgres] Stopped.")
