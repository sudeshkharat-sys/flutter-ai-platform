# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Flutter AI Studio portable package.
# Run from the repo root:  pyinstaller flutter_studio.spec

import os
from pathlib import Path

ROOT = Path(SPECPATH)
BACKEND = ROOT / "backend"
FRONTEND_BUILD = ROOT / "frontend" / "build"

block_cipher = None

a = Analysis(
    [str(BACKEND / "launcher.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=[
        # Jinja2 code-generation templates
        (str(BACKEND / "app" / "codegen" / "templates"), "app/codegen/templates"),
        # React production build (served as static files)
        (str(FRONTEND_BUILD), "frontend/build"),
    ],
    hiddenimports=[
        # uvicorn internals
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
        # SQLite / SQLAlchemy
        "aiosqlite",
        "sqlalchemy.dialects.sqlite",
        # App routers (imported dynamically via string in main.py)
        "app.api.models_router",
        "app.api.apps_router",
        "app.api.export_router",
        "app.api.master_router",
        "app.api.assets_router",
        "app.api.engine_router",
        # Misc
        "multipart",
        "passlib.handlers.bcrypt",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["psycopg2", "celery", "redis", "tkinter"],
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
    exclude_binaries=True,
    name="FlutterAIStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,   # keep console so users can see build logs
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
