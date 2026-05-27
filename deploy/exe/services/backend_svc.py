"""Starts the FastAPI/Uvicorn backend in-process for EXE deployment."""

import os
import sys
import threading
from pathlib import Path


def configure_env(base_dir: Path, db_user: str, db_password: str, db_name: str,
                  pg_port: int, redis_port: int) -> None:
    """Set all environment variables the backend reads from os.environ."""
    os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
    os.environ["POSTGRES_PORT"] = str(pg_port)
    os.environ["POSTGRES_DB"] = db_name
    os.environ["POSTGRES_USER"] = db_user
    os.environ["POSTGRES_PASSWORD"] = db_password

    redis_base = f"redis://127.0.0.1:{redis_port}"
    os.environ["REDIS_URL"] = f"{redis_base}/0"
    os.environ["CELERY_BROKER_URL"] = f"{redis_base}/3"
    os.environ["CELERY_RESULT_BACKEND"] = f"{redis_base}/4"

    # Flutter AI Studio data directories
    data_dir = base_dir / "data"
    os.environ["MODELS_DIR"] = str(data_dir / "models")
    os.environ["EXPORTS_DIR"] = str(data_dir / "exports")
    os.environ["REFERENCE_IMAGES_DIR"] = str(data_dir / "reference_images")

    # Disable ultralytics network calls — Zscaler / offline environments
    os.environ["ULTRALYTICS_AUTOUPDATE"] = "False"
    os.environ["YOLO_VERBOSE"] = "False"

    # Signal EXE mode and where the bundled React frontend lives
    os.environ["EXE_MODE"] = "true"
    if hasattr(sys, "_MEIPASS"):
        frontend_dir = str(Path(sys._MEIPASS) / "frontend_build")
    else:
        frontend_dir = str(Path(__file__).parent.parent.parent / "frontend" / "build")
    os.environ["FRONTEND_BUILD_DIR"] = frontend_dir

    # Ensure backend package is importable from PyInstaller bundle
    if hasattr(sys, "_MEIPASS"):
        backend_path = str(Path(sys._MEIPASS) / "backend")
    else:
        backend_path = str(Path(__file__).parent.parent.parent / "backend")

    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)

    (data_dir / "models").mkdir(parents=True, exist_ok=True)
    (data_dir / "exports").mkdir(parents=True, exist_ok=True)
    (data_dir / "reference_images").mkdir(parents=True, exist_ok=True)
    (base_dir / "logs").mkdir(parents=True, exist_ok=True)


def start(host: str = "127.0.0.1", port: int = 8000) -> threading.Thread:
    """Start uvicorn in a daemon thread and return it."""
    import uvicorn

    config = uvicorn.Config(
        "app.main:app",
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True, name="uvicorn")
    thread.start()
    return thread
