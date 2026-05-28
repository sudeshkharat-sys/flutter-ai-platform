"""Configures env and starts the FastAPI + Uvicorn backend thread."""

import os
import sys
import threading
from pathlib import Path


def configure_env(
    base_dir: Path,
    db_user: str,
    db_password: str,
    db_name: str,
    pg_port: int,
    redis_port: int,
    flutter_root: Path,
    java_home: Path,
    android_home: Path,
    gradle_zip: Path,
) -> None:
    """Set environment variables consumed by the FastAPI app and Celery tasks."""
    os.environ.update(
        {
            # PostgreSQL
            "POSTGRES_HOST":     "127.0.0.1",
            "POSTGRES_PORT":     str(pg_port),
            "POSTGRES_DB":       db_name,
            "POSTGRES_USER":     db_user,
            "POSTGRES_PASSWORD": db_password,
            # Redis / Celery
            "REDIS_URL":              f"redis://127.0.0.1:{redis_port}/0",
            "CELERY_BROKER_URL":      f"redis://127.0.0.1:{redis_port}/3",
            "CELERY_RESULT_BACKEND":  f"redis://127.0.0.1:{redis_port}/4",
            # File storage
            "MODELS_DIR":    str(base_dir / "data" / "models"),
            "EXPORTS_DIR":   str(base_dir / "data" / "exports"),
            "REFERENCE_IMAGES_DIR": str(base_dir / "data" / "reference_images"),
            # Build tools (used by build_apk.py and generator.py)
            "FLUTTER_ROOT":      str(flutter_root).replace("\\", "/"),
            "JAVA_HOME":         str(java_home),
            "ANDROID_HOME":      str(android_home).replace("\\", "/"),
            "GRADLE_ZIP_PATH":   str(gradle_zip).replace("\\", "/"),
            # Add build tools to PATH
            "PATH": (
                f"{java_home}\\bin;{flutter_root}\\bin;"
                f"{android_home}\\cmdline-tools\\latest\\bin;"
                f"{android_home}\\platform-tools;"
                + os.environ.get("PATH", "")
            ),
        }
    )

    # Ensure data directories exist
    for sub in ("models", "exports", "reference_images"):
        (base_dir / "data" / sub).mkdir(parents=True, exist_ok=True)
    (base_dir / "logs").mkdir(parents=True, exist_ok=True)


def start(host: str = "127.0.0.1", port: int = 8001) -> threading.Thread:
    """Start uvicorn in a daemon thread. Returns the thread."""

    if hasattr(sys, "_MEIPASS"):
        backend_path = str(Path(sys._MEIPASS) / "backend")
        if backend_path not in sys.path:
            sys.path.insert(0, backend_path)

    def _run():
        import uvicorn
        uvicorn.run(
            "app.main:app",
            host=host,
            port=port,
            log_level="info",
        )

    t = threading.Thread(target=_run, daemon=True, name="uvicorn")
    t.start()
    return t
