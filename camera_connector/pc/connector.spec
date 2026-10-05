# -*- mode: python ; coding: utf-8 -*-
# Single-file exe for the PC connector. Build:  pyinstaller connector.spec  (or build_exe.bat)
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ["cv2", "qrcode"]:
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

a = Analysis(["connector.py"], pathex=[], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             hookspath=[], runtime_hooks=[], excludes=["tkinter", "notebook", "IPython"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [], name="runner_connector",
          debug=False, strip=False, upx=False, console=True, icon="icon.ico")
