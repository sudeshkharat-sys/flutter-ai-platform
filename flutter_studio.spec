# -*- mode: python ; coding: utf-8 -*-
# Run from repo root:  pyinstaller flutter_studio.spec

from pathlib import Path

ROOT           = Path(SPECPATH)
BACKEND        = ROOT / "backend"
FRONTEND_BUILD = ROOT / "frontend" / "build"

block_cipher = None

a = Analysis(
    [str(BACKEND / "launcher.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=[
        (str(BACKEND / "app" / "codegen" / "templates"), "app/codegen/templates"),
        (str(FRONTEND_BUILD), "frontend/build"),
    ],
    hiddenimports=[
        # uvicorn
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # SQLAlchemy PostgreSQL driver
        "sqlalchemy.dialects.postgresql",
        "psycopg2",
        # Celery internals
        "celery",
        "celery.app",
        "celery.app.trace",
        "celery.__main__",
        "celery.bin.worker",
        "celery.concurrency",
        "celery.concurrency.solo",
        "kombu",
        "kombu.transport",
        "kombu.transport.redis",
        "redis",
        # App routers
        "app.api.models_router",
        "app.api.apps_router",
        "app.api.export_router",
        "app.api.master_router",
        "app.api.assets_router",
        "app.api.engine_router",
        # Tasks
        "app.tasks.build_apk",
        "app.tasks.convert_model",
        # Misc
        "multipart",
        "passlib.handlers.bcrypt",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FlutterAIStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    icon=None,
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
