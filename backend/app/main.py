import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.api import models_router, apps_router, export_router, master_router
from app.api import assets_router
from app.api import engine_router
from app.config import settings
from app.connectors.state_db import StateDBManager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure data dirs exist
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    settings.reference_images_dir.mkdir(parents=True, exist_ok=True)
    
    # Initialize PostgreSQL database
    db_manager = StateDBManager()
    db_manager.initialize_database()
    db_manager.create_tables_if_not_exists()
    
    # Check for missing model_asset_id column (schema auto-repair)
    try:
        from sqlalchemy import text
        engine = db_manager._get_engine(settings.POSTGRES_DB)
        with engine.connect() as conn:
            # PostgreSQL specific check for column existence
            check_sql = "SELECT column_name FROM information_schema.columns WHERE table_name='app_projects' AND column_name='model_asset_id'"
            res = conn.execute(text(check_sql)).fetchone()
            if not res:
                print("Repairing schema: Adding missing model_asset_id column to app_projects")
                conn.execute(text("ALTER TABLE app_projects ADD COLUMN model_asset_id VARCHAR"))
        engine.dispose()
    except Exception as e:
        print(f"Schema repair skip/error: {e}")
    
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


def _mount_spa(application: FastAPI) -> None:
    """Serve the React build as a SPA from FastAPI (used in EXE mode).

    When running under PyInstaller the frontend build is extracted to
    _MEIPASS/frontend_build.  In a plain dev checkout it lives at
    <repo>/frontend/build.  If neither directory exists (e.g. running the
    raw backend only) this function does nothing.
    """
    if hasattr(sys, "_MEIPASS"):
        build_dir = Path(sys._MEIPASS) / "frontend_build"
    else:
        build_dir = Path(__file__).parent.parent.parent / "frontend" / "build"

    if not build_dir.exists():
        return

    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    static_dir = build_dir / "static"
    if static_dir.exists():
        application.mount("/static", StaticFiles(directory=str(static_dir)), name="spa-static")

    @application.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        candidate = build_dir / full_path
        if candidate.exists() and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(build_dir / "index.html"))


_mount_spa(app)
