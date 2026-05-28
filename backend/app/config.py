import os
import sys
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings


def _get_app_base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent.parent


class Settings(BaseSettings):
    vision_platform_url: str = "http://localhost:8000"
    vision_platform_token: str = ""
    secret_key: str = "dev-secret-key"
    models_dir: Path = Path("./data/models")
    exports_dir: Path = Path("./data/exports")
    reference_images_dir: Path = Path("./data/reference_images")

    # PostgreSQL — set automatically by launcher from bundled PG
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: str = "5432"
    POSTGRES_DB: str = "flutter_studio"

    # Redis / Celery — set automatically by launcher from bundled Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/3"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/4"

    @property
    def postgres_url(self) -> str:
        if self.POSTGRES_PASSWORD:
            return (
                f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )
        return (
            f"postgresql://{self.POSTGRES_USER}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

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
