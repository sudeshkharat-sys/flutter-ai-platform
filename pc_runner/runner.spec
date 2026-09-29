# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for runner.py -- step 1 connectivity check only (no
# model/detection deps bundled yet; add ultralytics/torch collect_all()
# entries here, matching deploy/exe/launcher.spec's pattern, once step 2
# wires in the YOLO detector).
#
# Build:   pyinstaller runner.spec
# Output:  dist/runner.exe -- a single self-contained file (onefile mode).
#          Copy just this one .exe anywhere and double-click it to run --
#          no Python install, no accompanying folder needed.

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

for pkg in ["uvicorn", "fastapi", "starlette", "cv2"]:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
]

a = Analysis(
    ["runner.py"],
    pathex=[],
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="runner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon="icon.ico",
)
