# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for runner.py (Runner Cam PC runner + YOLO inspector). ultralytics / OpenCV are
# collected like deploy/exe/launcher.spec does; torch is picked up by PyInstaller's own hooks. Install
# the CPU-only torch wheel before building to keep the exe small (see build_exe.bat).
#
# Build:   pyinstaller runner.spec
# Output:  dist/runner.exe -- a single self-contained file (onefile mode).
#          Copy just this one .exe anywhere and double-click it to run --
#          no Python install, no accompanying folder needed.

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

for pkg in ["uvicorn", "fastapi", "starlette", "websockets", "ultralytics", "cv2", "PIL", "matplotlib"]:
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += ["case_logic", "inspector"]
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
    icon=None,
)
