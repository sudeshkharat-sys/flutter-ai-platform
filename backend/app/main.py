import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from app.api import models_router, apps_router, export_router, master_router
from app.api import assets_router
from app.api import engine_router
from app.config import settings
from app.connectors.state_db import StateDBManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    settings.reference_images_dir.mkdir(parents=True, exist_ok=True)

    db_manager = StateDBManager()
    db_manager.initialize_database()
    db_manager.create_tables_if_not_exists()

    yield


app = FastAPI(title="Flutter AI Studio", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(models_router.router, prefix="/api/v1")
app.include_router(apps_router.router, prefix="/api/v1")
app.include_router(export_router.router, prefix="/api/v1")
app.include_router(master_router.router, prefix="/api/v1")
app.include_router(assets_router.router, prefix="/api/v1")
app.include_router(engine_router.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "flutter-ai-studio"}


def _find_frontend_build() -> Path | None:
    """Locate the React production build regardless of how the app is launched."""
    candidates = []
    if getattr(sys, "frozen", False):
        # PyInstaller bundle: data files land inside _MEIPASS
        candidates.append(Path(sys._MEIPASS) / "frontend" / "build")
        candidates.append(Path(sys.executable).parent / "frontend" / "build")
    else:
        candidates.append(Path(__file__).parent.parent.parent / "frontend" / "build")

    for p in candidates:
        if p.is_dir():
            return p
    return None


_frontend = _find_frontend_build()
if _frontend:
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="static")
