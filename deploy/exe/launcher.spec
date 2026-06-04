# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Flutter AI Studio Windows EXE
#
# Build with:   pyinstaller launcher.spec
# Output:       D:\FlutterAI-App\FlutterAI\flutterai.exe
#
# Prerequisites: run build.bat — it handles all of this.

import sys
import os
import sysconfig
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

REPO_ROOT  = Path(SPECPATH).parent.parent       # flutter-ai-platform/
BACKEND    = REPO_ROOT / "backend"
FRONTEND   = REPO_ROOT / "frontend" / "build"
RESOURCES  = Path(SPECPATH) / "resources"

# ---------------------------------------------------------------------------
# Conda stdlib fix
# ---------------------------------------------------------------------------
_stdlib_dir     = sysconfig.get_path("stdlib")
_platstdlib_dir = sysconfig.get_path("platstdlib")
datas = []
if _stdlib_dir and os.path.isdir(_stdlib_dir):
    datas += [(_stdlib_dir, "lib-stdlib")]
if _platstdlib_dir and os.path.isdir(_platstdlib_dir) and _platstdlib_dir != _stdlib_dir:
    datas += [(_platstdlib_dir, "lib-platstdlib")]

binaries      = []
hiddenimports = []

hiddenimports += collect_submodules("encodings")

# matplotlib (needed by some ultralytics internals)
hiddenimports += collect_submodules("matplotlib")
datas         += collect_data_files("matplotlib")

for pkg in [
    "uvicorn", "fastapi", "starlette",
    "sqlalchemy", "psycopg2", "aiosqlite",
    "celery", "redis", "kombu", "billiard",
    "ultralytics",
    "PIL",
    "cv2",
    "onnx", "onnxruntime",
    "onnx2tf",
    "tf_keras",
    "jinja2",
    "pydantic", "pydantic_settings",
]:
    try:
        d, b, h = collect_all(pkg)
        datas         += d
        binaries      += b
        hiddenimports += h
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Backend source (exclude data/ — created at runtime)
# ---------------------------------------------------------------------------
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

# React build
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

# Build tools (bundled — version-locked)
for tool_dir, dest in [
    ("flutter",     "flutter"),
    ("jdk",         "jdk"),
    ("android-sdk", "android-sdk"),
    ("gradle",      "gradle"),
]:
    src = RESOURCES / tool_dir
    if src.exists():
        datas += [(str(src), dest)]
    else:
        print(f"WARNING: {tool_dir} not found at {src}")

# Launcher services package
datas += [(str(Path(SPECPATH) / "services"), "services")]
datas += [(str(Path(SPECPATH) / "sdk_check.py"), ".")]

# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------
hiddenimports += [
    "asyncpg",
    "asyncpg.pgproto.pgproto",
    "psycopg2",
    "aiosqlite",
    "celery.app.amqp",
    "celery.backends.redis",
    "celery.loaders.app",
    "kombu.transport.redis",
    # Celery tasks
    "app.tasks.convert_model",
    "app.tasks.build_apk",
    "app.tasks.celery_app",
    # Core app modules (lazy-imported inside functions — not caught by static analysis)
    "app.config",
    "app.database",
    "app.queries",
    # Connectors
    "app.connectors.state_db",
    "app.connectors.table_creation",
    # Models
    "app.models",
    "app.models.app_project",
    "app.models.master_mapping",
    "app.models.model_asset",
    # Schemas
    "app.schemas",
    "app.schemas.base",
    # Codegen (imported inside build_apk.py function body)
    "app.codegen",
    "app.codegen.generator",
    # API modules
    "app.api.models_router",
    "app.api.apps_router",
    "app.api.export_router",
    "app.api.master_router",
    "app.api.assets_router",
    "app.api.engine_router",
    # Uvicorn internals
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    # Auth
    "passlib.handlers.bcrypt",
    "passlib.handlers.pbkdf2",
    "passlib.utils.handlers",
    "passlib.crypto.digest",
    # Pydantic / multipart
    "email_validator",
    "multipart",
    # ONNX patch (app/__init__.py)
    "onnx.helper",
    "onnx_graphsurgeon",
    # Jinja2
    "jinja2.ext",
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
    excludes=["tkinter", "notebook", "IPython"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="flutterai",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(REPO_ROOT / "frontend" / "public" / "favicon.ico")
        if (REPO_ROOT / "frontend" / "public" / "favicon.ico").exists() else None,
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
