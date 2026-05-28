"""
build_portable.py  —  Assemble the fully self-contained Flutter AI Studio package.

Run once on a Windows machine with Python 3.11+ and Node.js 18+ installed:

    python build_portable.py

What it downloads and bundles
------------------------------
  PostgreSQL 15  (postgres.exe, pg_ctl.exe, initdb.exe …)
  Redis 5        (redis-server.exe)
  Flutter SDK    (flutter.bat, dart.bat …)
  JDK 17         (java.exe …)
  Android SDK    (sdkmanager, platform-tools, build-tools, android-35)
  Python + all libs  (via PyInstaller)
  React build    (static files served by FastAPI)

Output
------
  dist/FlutterAIStudio/          ← unzip and double-click FlutterAIStudio.exe
  dist/FlutterAIStudio.zip       ← share this (~5-8 GB)
"""

import os
import sys
import shutil
import subprocess
import urllib.request
import zipfile
import time
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
DIST = ROOT / "dist"
PKG  = DIST / "FlutterAIStudio"
DL   = ROOT / "_downloads"

# ── Download URLs ──────────────────────────────────────────────────────────────

FLUTTER_URL  = (
    "https://storage.googleapis.com/flutter_infra_release/releases/stable/windows/"
    "flutter_windows_3.24.5-stable.zip"
)
FLUTTER_ZIP  = "flutter_windows_3.24.5-stable.zip"

# Microsoft OpenJDK 17 Windows x64
JDK_URL  = "https://aka.ms/download-jdk/microsoft-jdk-17.0.14-windows-x64.zip"
JDK_ZIP  = "microsoft-jdk-17-windows-x64.zip"

# Android command-line tools
ANDROID_URL  = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"
ANDROID_ZIP  = "commandlinetools-win.zip"
ANDROID_PLATFORM    = "android-35"
ANDROID_BUILD_TOOLS = "35.0.0"

# PostgreSQL 15 Windows x64 binaries (EDB zip — ~250 MB)
POSTGRES_URL = (
    "https://get.enterprisedb.com/postgresql/postgresql-15.6-1-windows-x64-binaries.zip"
)
POSTGRES_ZIP = "postgresql-15-windows-x64-binaries.zip"

# Redis for Windows (unofficial port by tporadowski, last stable build)
REDIS_URL = (
    "https://github.com/tporadowski/redis/releases/download/v5.0.14.1/"
    "Redis-x64-5.0.14.1.zip"
)
REDIS_ZIP = "Redis-x64-5.0.14.1.zip"

# ── Helpers ────────────────────────────────────────────────────────────────────

def step(title: str):
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def run(cmd, cwd=None, env=None, check=True):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, cwd=cwd, env=env, check=check)


def download(url: str, dest: Path):
    if dest.exists():
        print(f"  [skip] {dest.name} already downloaded")
        return
    print(f"  Downloading {dest.name} …")
    DL.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    with urllib.request.urlopen(url) as resp, open(tmp, "wb") as fh:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        block = 1 << 20
        while True:
            data = resp.read(block)
            if not data:
                break
            fh.write(data)
            downloaded += len(data)
            if total:
                pct = downloaded * 100 // total
                print(f"\r    {pct:3d}%  ({downloaded >> 20} / {total >> 20} MB)", end="", flush=True)
    print()
    tmp.rename(dest)


def extract_zip(archive: Path, dest: Path):
    print(f"  Extracting {archive.name} → {dest.name}/")
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)


# ── Steps ──────────────────────────────────────────────────────────────────────

def build_frontend():
    step("1/9  Build React frontend")
    fe = ROOT / "frontend"
    if not (fe / "node_modules").exists():
        run(["npm", "install"], cwd=fe)
    run(["npm", "run", "build"], cwd=fe)


def download_postgres():
    step("2/9  Download PostgreSQL 15 binaries")
    archive = DL / POSTGRES_ZIP
    download(POSTGRES_URL, archive)
    dest = PKG / "postgresql"
    if dest.exists():
        print("  [skip] PostgreSQL already extracted")
        return
    tmp = PKG / "_pg_tmp"
    extract_zip(archive, tmp)
    # EDB zip extracts to pgsql/
    inner = tmp / "pgsql"
    if not inner.exists():
        inner = next(tmp.iterdir())
    inner.rename(dest)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"  PostgreSQL → {dest}")


def download_redis():
    step("3/9  Download Redis for Windows")
    archive = DL / REDIS_ZIP
    download(REDIS_URL, archive)
    dest = PKG / "redis"
    if dest.exists():
        print("  [skip] Redis already extracted")
        return
    extract_zip(archive, dest)
    print(f"  Redis → {dest}")


def download_flutter():
    step("4/9  Download Flutter SDK")
    archive = DL / FLUTTER_ZIP
    download(FLUTTER_URL, archive)
    dest = PKG / "flutter"
    if dest.exists():
        print("  [skip] Flutter already extracted")
        return
    tmp = PKG / "_fl_tmp"
    extract_zip(archive, tmp)
    inner = tmp / "flutter"
    if not inner.exists():
        inner = next(tmp.iterdir())
    inner.rename(dest)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"  Flutter → {dest}")


def download_jdk():
    step("5/9  Download JDK 17")
    archive = DL / JDK_ZIP
    download(JDK_URL, archive)
    dest = PKG / "jdk-17"
    if dest.exists():
        print("  [skip] JDK already extracted")
        return
    tmp = PKG / "_jdk_tmp"
    extract_zip(archive, tmp)
    inner = next(tmp.iterdir())
    inner.rename(dest)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"  JDK → {dest}")


def setup_android_sdk():
    step("6/9  Download & configure Android SDK")
    archive = DL / ANDROID_ZIP
    download(ANDROID_URL, archive)

    sdk       = PKG / "android-sdk"
    tools_dir = sdk / "cmdline-tools" / "latest"
    tools_dir.mkdir(parents=True, exist_ok=True)

    if not (tools_dir / "bin").exists():
        tmp = sdk / "_tools_tmp"
        extract_zip(archive, tmp)
        inner = tmp / "cmdline-tools"
        if not inner.exists():
            inner = tmp
        for item in inner.iterdir():
            shutil.move(str(item), str(tools_dir))
        shutil.rmtree(tmp, ignore_errors=True)

    sdkmanager = tools_dir / "bin" / "sdkmanager.bat"
    if not sdkmanager.exists():
        print(f"  WARNING: sdkmanager not found")
        return

    env = os.environ.copy()
    jdk_bin = PKG / "jdk-17" / "bin"
    env["JAVA_HOME"]    = str(PKG / "jdk-17")
    env["ANDROID_HOME"] = str(sdk)
    env["PATH"]         = str(jdk_bin) + os.pathsep + env.get("PATH", "")

    proc = subprocess.Popen(
        [str(sdkmanager), "--licenses", f"--sdk_root={sdk}"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, env=env,
    )
    try:
        proc.communicate(input=b"y\n" * 20, timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()

    pkgs = ["platform-tools", f"platforms;{ANDROID_PLATFORM}", f"build-tools;{ANDROID_BUILD_TOOLS}"]
    run([str(sdkmanager), f"--sdk_root={sdk}"] + pkgs, env=env, check=False)
    print("  Android SDK ready.")


def install_python_deps():
    step("7/9  Install Python backend dependencies")
    run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "backend" / "requirements.txt")])


def run_pyinstaller():
    step("8/9  PyInstaller — bundle Python + backend")
    run([
        sys.executable, "-m", "PyInstaller",
        str(ROOT / "flutter_studio.spec"),
        "--distpath", str(DIST),
        "--workpath", str(ROOT / "build_work"),
        "--noconfirm",
    ], cwd=ROOT)


def assemble_and_zip():
    step("9/9  Assemble + zip")
    (PKG / "data").mkdir(exist_ok=True)

    (PKG / "README.txt").write_text(
        "Flutter AI Studio — Portable\n"
        "============================\n\n"
        "1. Double-click FlutterAIStudio.exe\n"
        "2. Wait ~10 seconds for all services to start\n"
        "3. Browser opens automatically at http://localhost:8001\n\n"
        "Bundled services (start/stop automatically):\n"
        "  PostgreSQL 15  →  postgresql/\n"
        "  Redis 5        →  redis/\n"
        "  Celery worker  →  (runs inside the exe)\n\n"
        "Bundled build tools:\n"
        "  Flutter SDK    →  flutter/\n"
        "  JDK 17         →  jdk-17/\n"
        "  Android SDK    →  android-sdk/\n\n"
        "All app data is stored in  data/\n"
        "Logs: data/postgres.log\n"
    )

    zip_path = DIST / "FlutterAIStudio.zip"
    if zip_path.exists():
        zip_path.unlink()
    print(f"  Zipping {PKG} … (may take several minutes)")
    shutil.make_archive(str(DIST / "FlutterAIStudio"), "zip", str(DIST), "FlutterAIStudio")
    size_gb = zip_path.stat().st_size / (1 << 30)
    print(f"  Done! {zip_path}  ({size_gb:.1f} GB)")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--skip-frontend",    action="store_true")
    p.add_argument("--skip-downloads",   action="store_true")
    p.add_argument("--skip-pyinstaller", action="store_true")
    p.add_argument("--skip-zip",         action="store_true")
    args = p.parse_args()

    PKG.mkdir(parents=True, exist_ok=True)

    if not args.skip_frontend:
        build_frontend()

    if not args.skip_downloads:
        download_postgres()
        download_redis()
        download_flutter()
        download_jdk()
        setup_android_sdk()

    install_python_deps()

    if not args.skip_pyinstaller:
        run_pyinstaller()

    assemble_and_zip()

    print()
    print("=" * 60)
    print("  BUILD COMPLETE")
    print(f"  Package : {PKG}")
    print(f"  Zip     : {DIST / 'FlutterAIStudio.zip'}")
    print("=" * 60)
