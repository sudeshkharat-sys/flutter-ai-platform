"""
Flutter AI Studio — portable launcher.
Double-click FlutterAIStudio.exe (or run this script) to start the server
and open the browser automatically.
"""
import os
import sys
import time
import threading
import webbrowser
from pathlib import Path


def _get_app_base() -> Path:
    """Directory that contains the exe (or the repo root when running as script)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


def _setup_env(app_base: Path):
    """Point environment variables at the bundled Flutter / Android / JDK."""
    flutter_root = app_base / "flutter"
    java_home = app_base / "jdk-17"
    android_home = app_base / "android-sdk"

    if java_home.exists():
        os.environ["JAVA_HOME"] = str(java_home)
    if android_home.exists():
        os.environ["ANDROID_HOME"] = str(android_home)
    if flutter_root.exists():
        os.environ["FLUTTER_ROOT"] = str(flutter_root)

    extra_paths = []
    if (java_home / "bin").exists():
        extra_paths.append(str(java_home / "bin"))
    if (flutter_root / "bin").exists():
        extra_paths.append(str(flutter_root / "bin"))
    if (android_home / "cmdline-tools" / "latest" / "bin").exists():
        extra_paths.append(str(android_home / "cmdline-tools" / "latest" / "bin"))
    if (android_home / "platform-tools").exists():
        extra_paths.append(str(android_home / "platform-tools"))

    if extra_paths:
        os.environ["PATH"] = os.pathsep.join(extra_paths) + os.pathsep + os.environ.get("PATH", "")

    # SQLite database lives in <app_base>/data/
    data_dir = app_base / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["SQLITE_DB_PATH"] = str(data_dir / "flutter_studio.db")

    # Absolute data dirs so relative ./data/* paths in config.py always resolve
    os.chdir(str(app_base))


def _open_browser_later(url: str, delay: float = 3.0):
    def _open():
        time.sleep(delay)
        webbrowser.open(url)
    threading.Thread(target=_open, daemon=True).start()


def main():
    app_base = _get_app_base()
    _setup_env(app_base)

    # When frozen by PyInstaller the backend package is in _MEIPASS;
    # when running as a plain script we need the backend/ folder on sys.path.
    if not getattr(sys, "frozen", False):
        backend_dir = Path(__file__).parent
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))

    port = int(os.environ.get("PORT", 8001))
    url = f"http://localhost:{port}"

    print("=" * 55)
    print("  Flutter AI Studio")
    print("=" * 55)
    print(f"  Server  : {url}")
    print(f"  Data    : {app_base / 'data'}")
    print(f"  Flutter : {app_base / 'flutter'}")
    print(f"  JDK     : {app_base / 'jdk-17'}")
    print("=" * 55)
    print("  Opening browser in 3 seconds… (Ctrl+C to stop)")
    print()

    _open_browser_later(url, delay=3.0)

    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
