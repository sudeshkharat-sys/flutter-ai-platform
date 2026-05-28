"""
build_portable.py  —  Assemble the fully self-contained Flutter AI Studio package.

Run once on a Windows machine that has Python 3.11+ and Node.js 18+ installed:

    python build_portable.py

What it does
------------
1. Builds the React frontend (npm run build)
2. Downloads Flutter SDK for Windows (x64)
3. Downloads JDK 17 (Microsoft OpenJDK)
4. Downloads Android command-line tools
5. Accepts Android SDK licenses, installs platform-tools + build-tools + android-35
6. Installs Python backend dependencies (site-packages)
7. Runs PyInstaller to create the .exe + bundled Python
8. Assembles everything into  dist/FlutterAIStudio/
9. Zips the folder into     dist/FlutterAIStudio.zip

The final zip is ~4-7 GB.  Share it — recipient just unzips and
double-clicks FlutterAIStudio/FlutterAIStudio.exe.
"""

import os
import sys
import shutil
import subprocess
import urllib.request
import zipfile
import tarfile
import hashlib
import stat
import time
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
DIST = ROOT / "dist"
PKG  = DIST / "FlutterAIStudio"

# ── Download URLs ─────────────────────────────────────────────────────────────
# Flutter stable for Windows x64
FLUTTER_ZIP_URL = (
    "https://storage.googleapis.com/flutter_infra_release/releases/stable/windows/"
    "flutter_windows_3.24.5-stable.zip"
)
FLUTTER_ZIP_NAME = "flutter_windows_3.24.5-stable.zip"

# Microsoft OpenJDK 17 (Windows x64 zip)
JDK_ZIP_URL = (
    "https://aka.ms/download-jdk/microsoft-jdk-17.0.14-windows-x64.zip"
)
JDK_ZIP_NAME = "microsoft-jdk-17-windows-x64.zip"

# Android command-line tools (latest)
ANDROID_TOOLS_URL = (
    "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"
)
ANDROID_TOOLS_NAME = "commandlinetools-win.zip"

# Android platform / build-tools versions to install
ANDROID_PLATFORM  = "android-35"
ANDROID_BUILD_TOOLS = "35.0.0"
# ─────────────────────────────────────────────────────────────────────────────


def run(cmd, cwd=None, env=None, check=True):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, cwd=cwd, env=env, check=check)
    return result


def download(url: str, dest: Path):
    if dest.exists():
        print(f"  [skip] {dest.name} already downloaded")
        return
    print(f"  Downloading {dest.name} …")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    with urllib.request.urlopen(url) as resp, open(tmp, "wb") as fh:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        block = 1 << 20  # 1 MB
        while True:
            data = resp.read(block)
            if not data:
                break
            fh.write(data)
            downloaded += len(data)
            if total:
                pct = downloaded * 100 // total
                print(f"\r    {pct:3d}% ({downloaded >> 20} / {total >> 20} MB)", end="", flush=True)
    print()
    tmp.rename(dest)
    print(f"  Saved {dest.name}")


def extract_zip(archive: Path, dest: Path):
    print(f"  Extracting {archive.name} → {dest.name}/")
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)


def step(title: str):
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


# ── Step 1: Build React frontend ──────────────────────────────────────────────
def build_frontend():
    step("1/8  Build React frontend")
    frontend = ROOT / "frontend"
    if not (frontend / "node_modules").exists():
        run(["npm", "install"], cwd=frontend)
    run(["npm", "run", "build"], cwd=frontend)
    print("  React build ready.")


# ── Step 2: Download Flutter SDK ──────────────────────────────────────────────
def download_flutter():
    step("2/8  Download Flutter SDK")
    dl_dir = ROOT / "_downloads"
    archive = dl_dir / FLUTTER_ZIP_NAME
    download(FLUTTER_ZIP_URL, archive)
    flutter_dest = PKG / "flutter"
    if flutter_dest.exists():
        print("  [skip] Flutter already extracted")
    else:
        tmp = PKG / "_flutter_tmp"
        extract_zip(archive, tmp)
        # The zip contains a top-level 'flutter' folder
        inner = tmp / "flutter"
        if not inner.exists():
            # Some archives don't have the inner folder
            inner = next(tmp.iterdir())
        inner.rename(flutter_dest)
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"  Flutter → {flutter_dest}")


# ── Step 3: Download JDK 17 ───────────────────────────────────────────────────
def download_jdk():
    step("3/8  Download JDK 17")
    dl_dir = ROOT / "_downloads"
    archive = dl_dir / JDK_ZIP_NAME
    download(JDK_ZIP_URL, archive)
    jdk_dest = PKG / "jdk-17"
    if jdk_dest.exists():
        print("  [skip] JDK already extracted")
    else:
        tmp = PKG / "_jdk_tmp"
        extract_zip(archive, tmp)
        # The zip contains one top-level folder (e.g. jdk-17.0.14+7)
        inner = next(tmp.iterdir())
        inner.rename(jdk_dest)
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"  JDK → {jdk_dest}")


# ── Step 4: Download + configure Android SDK ─────────────────────────────────
def setup_android_sdk():
    step("4/8  Download & configure Android SDK")
    dl_dir = ROOT / "_downloads"
    archive = dl_dir / ANDROID_TOOLS_NAME
    download(ANDROID_TOOLS_URL, archive)

    sdk = PKG / "android-sdk"
    tools_dir = sdk / "cmdline-tools" / "latest"
    tools_dir.mkdir(parents=True, exist_ok=True)

    if (tools_dir / "bin").exists():
        print("  [skip] Android command-line tools already extracted")
    else:
        tmp = sdk / "_tools_tmp"
        extract_zip(archive, tmp)
        # Archive extracts to  cmdline-tools/  inside the zip
        inner = tmp / "cmdline-tools"
        if not inner.exists():
            inner = tmp
        for item in inner.iterdir():
            shutil.move(str(item), str(tools_dir))
        shutil.rmtree(tmp, ignore_errors=True)

    # sdkmanager lives in tools_dir/bin/
    sdkmanager = tools_dir / "bin" / "sdkmanager.bat"
    if not sdkmanager.exists():
        print(f"  WARNING: sdkmanager not found at {sdkmanager}")
        return

    env = os.environ.copy()
    jdk_bin = PKG / "jdk-17" / "bin"
    env["JAVA_HOME"] = str(PKG / "jdk-17")
    env["ANDROID_HOME"] = str(sdk)
    env["PATH"] = str(jdk_bin) + os.pathsep + env.get("PATH", "")

    # Accept licenses
    print("  Accepting Android SDK licenses…")
    proc = subprocess.Popen(
        [str(sdkmanager), "--licenses", f"--sdk_root={sdk}"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    # Send 'y' for every prompt (up to 20)
    try:
        out, _ = proc.communicate(input=b"y\n" * 20, timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()

    # Install required packages
    packages = [
        "platform-tools",
        f"platforms;{ANDROID_PLATFORM}",
        f"build-tools;{ANDROID_BUILD_TOOLS}",
    ]
    print(f"  Installing packages: {packages}")
    run(
        [str(sdkmanager), f"--sdk_root={sdk}"] + packages,
        env=env,
        check=False,  # non-fatal if network is slow
    )
    print("  Android SDK ready.")


# ── Step 5: Install Python dependencies ──────────────────────────────────────
def install_python_deps():
    step("5/8  Install Python backend dependencies")
    req = ROOT / "backend" / "requirements.txt"
    run([sys.executable, "-m", "pip", "install", "-r", str(req)])


# ── Step 6: Run PyInstaller ───────────────────────────────────────────────────
def run_pyinstaller():
    step("6/8  PyInstaller — bundle Python backend")
    spec = ROOT / "flutter_studio.spec"
    run(
        [sys.executable, "-m", "PyInstaller", str(spec), "--distpath", str(DIST), "--workpath", str(ROOT / "build_work"), "--noconfirm"],
        cwd=ROOT,
    )
    print("  PyInstaller done.")


# ── Step 7: Copy tools into the PyInstaller output folder ────────────────────
def copy_tools():
    step("7/8  Assemble final package")
    # PyInstaller already wrote FlutterAIStudio/ into DIST.
    # The Flutter/JDK/Android dirs are already in PKG from earlier steps.
    # Just make sure the data dir exists.
    (PKG / "data").mkdir(exist_ok=True)

    # Copy a starter README
    readme = PKG / "README.txt"
    readme.write_text(
        "Flutter AI Studio — Portable\n"
        "============================\n\n"
        "1. Double-click  FlutterAIStudio.exe\n"
        "2. The app opens at  http://localhost:8001\n\n"
        "Bundled tools:\n"
        f"  Flutter  → flutter/\n"
        f"  JDK 17   → jdk-17/\n"
        f"  Android  → android-sdk/\n\n"
        "All app data is stored in  data/\n"
    )
    print(f"  Package assembled at  {PKG}")


# ── Step 8: Zip everything ────────────────────────────────────────────────────
def create_zip():
    step("8/8  Create distributable zip")
    zip_path = DIST / "FlutterAIStudio.zip"
    if zip_path.exists():
        zip_path.unlink()
    print(f"  Zipping {PKG} → {zip_path}  (this may take several minutes…)")
    shutil.make_archive(str(DIST / "FlutterAIStudio"), "zip", str(DIST), "FlutterAIStudio")
    size_gb = zip_path.stat().st_size / (1 << 30)
    print(f"  Done! {zip_path}  ({size_gb:.1f} GB)")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build the portable Flutter AI Studio package")
    parser.add_argument("--skip-frontend",  action="store_true", help="Skip npm build")
    parser.add_argument("--skip-downloads", action="store_true", help="Skip downloading Flutter/JDK/Android")
    parser.add_argument("--skip-pyinstaller", action="store_true", help="Skip PyInstaller step")
    parser.add_argument("--skip-zip",       action="store_true", help="Skip final zip creation")
    args = parser.parse_args()

    PKG.mkdir(parents=True, exist_ok=True)

    if not args.skip_frontend:
        build_frontend()

    if not args.skip_downloads:
        download_flutter()
        download_jdk()
        setup_android_sdk()

    install_python_deps()

    if not args.skip_pyinstaller:
        run_pyinstaller()

    copy_tools()

    if not args.skip_zip:
        create_zip()

    print()
    print("=" * 60)
    print("  BUILD COMPLETE")
    print(f"  Package : {PKG}")
    print(f"  Zip     : {DIST / 'FlutterAIStudio.zip'}")
    print("=" * 60)
