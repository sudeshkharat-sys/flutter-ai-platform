# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Flutter AI Studio Windows EXE
#
# Build with:   pyinstaller launcher.spec
# Output:       dist/FlutterAI/flutterai.exe
#
# Prerequisites (run build.bat — it handles all of this):
#   pip install pyinstaller
#   CPU-only torch pre-installed (see build.bat)
#   Place portable PostgreSQL 17 in:  deploy/exe/resources/postgres/
#   Place portable Redis 7 in:        deploy/exe/resources/redis/
#   Build React frontend:             cd frontend && npm run build

import sys
import os
import sysconfig
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

REPO_ROOT = Path(SPECPATH).parent.parent        # flutter-ai-platform/
BACKEND   = REPO_ROOT / "backend"
FRONTEND  = REPO_ROOT / "frontend" / "build"
RESOURCES = Path(SPECPATH) / "resources"

# ---------------------------------------------------------------------------
# Conda stdlib fix
# ---------------------------------------------------------------------------
_stdlib_dir = sysconfig.get_path("stdlib")
_platstdlib_dir = sysconfig.get_path("platstdlib")
datas = []
if _stdlib_dir and os.path.isdir(_stdlib_dir):
    datas += [(_stdlib_dir, "lib-stdlib")]
if _platstdlib_dir and os.path.isdir(_platstdlib_dir) and _platstdlib_dir != _stdlib_dir:
    datas += [(_platstdlib_dir, "lib-platstdlib")]

binaries  = []
hiddenimports = []

hiddenimports += collect_submodules("encodings")

from PyInstaller.utils.hooks import collect_data_files

for pkg in [
    "uvicorn", "fastapi", "starlette",
    "sqlalchemy", "asyncpg", "psycopg2",
    "aiosqlite",
    "celery", "redis", "kombu", "billiard",
    "ultralytics",
    "onnxruntime",
    "onnx",
    "onnx2tf",
    "PIL",
    "cv2",
    "torch", "torchvision",
    "matplotlib",       # ultralytics imports matplotlib internally during YOLO load
    "tf_keras",         # required by onnx2tf / TFLite export
    "tensorflow",       # pulled in by tf_keras / onnx2tf
    "botocore",         # optional ultralytics cloud dep
    "tensorboard",      # optional ultralytics training dep
]:
    try:
        d, b, h = collect_all(pkg)
        datas    += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# Backend source — copy only app code, never data/ (models & uploads are
# created fresh at runtime and must never be bundled into the EXE)
for item in BACKEND.iterdir():
    if item.name in ("data", "__pycache__", ".env", ".env.example"):
        continue
    if item.is_dir():
        for f in item.rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts:
                rel_dest = "backend/" + str(f.relative_to(BACKEND).parent).replace("\\", "/")
                datas += [(str(f), rel_dest)]
    elif item.is_file():
        datas += [(str(item), "backend")]

# React build artefacts
if FRONTEND.exists():
    datas += [(str(FRONTEND), "frontend_build")]
else:
    print(f"WARNING: React build not found at {FRONTEND}. Run 'npm run build' first.")

# Portable service binaries
pg_bin = RESOURCES / "postgres"
if pg_bin.exists():
    datas += [(str(pg_bin), "postgres")]
else:
    print(f"WARNING: Portable PostgreSQL not found at {pg_bin}")

redis_bin = RESOURCES / "redis"
if redis_bin.exists():
    datas += [(str(redis_bin), "redis")]
else:
    print(f"WARNING: Portable Redis not found at {redis_bin}")

# Services package (launcher helpers)
datas += [(str(Path(SPECPATH) / "services"), "services")]

# Additional hidden imports
hiddenimports += [
    "asyncpg",
    "asyncpg.pgproto.pgproto",
    "psycopg2",
    "aiosqlite",
    "celery.app.amqp",
    "celery.backends.redis",
    "celery.loaders.app",
    "kombu.transport.redis",
    # Flutter AI task modules
    "app.tasks.build_apk",
    "app.tasks.convert_model",
    "app.tasks.celery_app",
    # API routes
    "app.api.models_router",
    "app.api.apps_router",
    "app.api.export_router",
    "app.api.master_router",
    "app.api.assets_router",
    "app.api.engine_router",
    # Connectors
    "app.connectors.state_db",
    "app.connectors.table_creation",
    # Uvicorn internals
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "email_validator",
    "multipart",
    "passlib.handlers.pbkdf2",
    "passlib.handlers.bcrypt",
    "passlib.handlers.sha2_crypt",
    "passlib.utils.pbkdf2",
    "passlib.utils.handlers",
    "passlib.utils.binary",
    "passlib.utils.binary",
    "passlib.crypto.digest",
    # matplotlib backends — ultralytics imports these transitively
    "matplotlib",
    "matplotlib.pyplot",
    "matplotlib.backends.backend_agg",
    # ONNX / TFLite conversion pipeline
    "onnx",
    "onnxslim",
    "onnxscript",
    "onnx_graphsurgeon",
    "sng4onnx",
    "onnx2tf",
    "tf_keras",
]

# ---------------------------------------------------------------------------
a = Analysis(
    ["launcher.py"],
    pathex=[str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "notebook", "IPython", "transformers",
        # torch distributed training — absent in CPU-only wheel, not needed for inference
        "torch.distributed._shard",
        "torch.distributed._sharded_tensor",
        "torch.distributed._sharding_spec",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # --onedir keeps binaries separate (faster startup)
    name="flutterai",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # UPX can corrupt DLLs — keep off
    console=True,               # Show console so users can see service status
    icon=str(REPO_ROOT / "frontend" / "public" / "favicon.ico") if (REPO_ROOT / "frontend" / "public" / "favicon.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FlutterAI",
)
