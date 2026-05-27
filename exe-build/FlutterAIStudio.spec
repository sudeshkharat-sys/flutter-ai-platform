# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for FlutterAIStudio.exe
# Run from the project root:   pyinstaller exe-build/FlutterAIStudio.spec
#

import sys
from pathlib import Path

ROOT    = Path(SPECPATH).parent          # flutter-ai-platform/
BACKEND = ROOT / "backend"
BUILD   = ROOT / "exe-build"

block_cipher = None

# ── Hidden imports ─────────────────────────────────────────────────────────────
# These modules are discovered at runtime (not via static import analysis).

hidden_imports = [
    # FastAPI / Starlette internals
    "uvicorn.lifespan.on",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.logging",
    "starlette.routing",
    "starlette.staticfiles",
    "starlette.responses",
    "fastapi.middleware.cors",

    # SQLAlchemy dialects
    "sqlalchemy.dialects.postgresql",
    "sqlalchemy.dialects.postgresql.psycopg2",

    # Celery internals
    "celery.app.amqp",
    "celery.backends.redis",
    "celery.loaders.app",
    "celery.concurrency.solo",
    "celery.fixups.django",
    "kombu.transport.redis",
    "kombu.serialization",
    "billiard.pool",

    # App tasks (dynamically imported by Celery)
    "app.tasks.convert_model",
    "app.tasks.build_apk",
    "app.tasks.celery_app",

    # App modules
    "app.main",
    "app.config",
    "app.api",
    "app.api.models_router",
    "app.api.apps_router",
    "app.api.export_router",
    "app.api.master_router",
    "app.api.assets_router",
    "app.connectors.state_db",
    "app.database",
    "app.queries",
    "app.schemas",
    "app.codegen.generator",
    "app.models",

    # ML / model conversion
    "ultralytics",
    "ultralytics.models",
    "ultralytics.utils",
    "onnx",
    "onnxruntime",
    "onnx2tf",
    "tf_keras",
    "PIL",
    "PIL.Image",
    "cv2",
    "numpy",

    # Misc
    "multiprocessing.pool",
    "multiprocessing.spawn",
    "pkg_resources",
    "passlib.handlers.bcrypt",
    "jose",
    "aiofiles",
    "psycopg2",
]

# ── Data files (non-Python assets) ────────────────────────────────────────────

datas = [
    # Backend templates (Jinja2 .j2 files for Flutter codegen)
    (str(BACKEND / "app" / "codegen" / "templates"), "app/codegen/templates"),
    # Any static assets in the backend
    (str(BACKEND / "app" / "api"),   "app/api"),
]

# ── Analysis ───────────────────────────────────────────────────────────────────

a = Analysis(
    [str(BUILD / "launcher.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[str(BUILD / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude test frameworks to save space
        "pytest", "IPython", "notebook", "matplotlib",
        "tkinter", "_tkinter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # onedir mode (faster startup than onefile)
    name="FlutterAIStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,                   # Console window shows service status
    icon=str(BUILD / "icon.ico") if (BUILD / "icon.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="FlutterAIStudio",
)
