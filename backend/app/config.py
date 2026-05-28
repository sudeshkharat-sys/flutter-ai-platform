import os
import sys
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings


def _get_app_base() -> Path:
    """Return the directory where the app is rooted (exe dir or repo root)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent.parent


def _default_db_path() -> str:
    db_env = os.environ.get("SQLITE_DB_PATH")
    if db_env:
        return db_env
    return str(_get_app_base() / "data" / "flutter_studio.db")


class Settings(BaseSettings):
    vision_platform_url: str = "http://localhost:8000"
    vision_platform_token: str = ""
    secret_key: str = "dev-secret-key"
    models_dir: Path = Path("./data/models")
    exports_dir: Path = Path("./data/exports")
    reference_images_dir: Path = Path("./data/reference_images")

    sqlite_db_path: str = ""

    @property
    def database_url(self) -> str:
        path = self.sqlite_db_path or _default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path}"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "protected_namespaces": ("settings_",),
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
