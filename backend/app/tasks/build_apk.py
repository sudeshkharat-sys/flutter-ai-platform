import os
import subprocess
import zipfile
import io
import shutil
import json
import time
import tempfile
from pathlib import Path
from app.tasks.celery_app import celery_app
from app.config import settings
from app.codegen.generator import generate_flutter_project

def _get_db():
    from app.connectors.state_db import StateDBConnector
    return StateDBConnector()

def _update_status(db, app_id, status=None, step=None, error=None, apk_path=None, log_append=None):
    from app.queries import ProjectQueries
    rows = db.execute_query(ProjectQueries.GET_PROJECT_BY_ID, {"id": app_id})
    if not rows: return
    app = dict(rows[0])
    
    new_status = status if status is not None else app.get("build_status")
    new_step = step if step is not None else app.get("build_step")
    new_error = error if error is not None else None
    new_apk_path = apk_path if apk_path is not None else app.get("apk_path")
    
    current_log = app.get("build_log", "") or ""
    if log_append:
        if not isinstance(log_append, str):
            try: log_append = str(log_append)
            except: log_append = "[Encoding Error]"
        
        sanitized_append = "".join(c if ord(c) < 128 else " " for c in log_append)
        current_log += sanitized_append

    def serialize(val):
        if val is None: return None
        if isinstance(val, str): return val
        return json.dumps(val)

    db.execute_update(ProjectQueries.UPDATE_PROJECT, {
        "id": app_id,
        "name": app.get("name"),
        "package_name": app.get("package_name"),
        "model_asset_id": app.get("model_asset_id"),
        "model_asset_ids": serialize(app.get("model_asset_ids")),
        "inspection_tasks": serialize(app.get("inspection_tasks")),
        "canvas_state": serialize(app.get("canvas_state")),
        "app_settings": serialize(app.get("app_settings")),
        "build_status": new_status,
        "build_step": new_step,
        "build_log": current_log,
        "apk_path": new_apk_path
    })

def _run_command_streaming(db, app_id, cmd, cwd, env):
    """Run a command and stream its output to the build_log in batches."""
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
        encoding='utf-8',
        errors='replace'
    )
    
    batch = []
    import time
    last_update = time.time()

    for line in process.stdout:
        batch.append(line)
        if time.time() - last_update > 2 or len(batch) >= 20:
            _update_status(db, app_id, log_append="".join(batch))
            batch = []
            last_update = time.time()
            
    process.wait()
    if batch:
        _update_status(db, app_id, log_append="".join(batch))
    return process.returncode

def _safe_extract(zip_bytes, extract_path):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for member in zf.infolist():
            zf.extract(member, extract_path)

def _find_project_dir(extract_path: Path) -> Path:
    """
    Return the Flutter project root inside extract_path.

    We search for pubspec.yaml rather than relying on next(iterdir())
    because iterdir() order is OS-dependent and any extra file landing
    at the root level (e.g. __MACOSX metadata) would cause the wrong
    directory to be used, making every subsequent flutter command fail
    with "No pubspec.yaml file found".
    """
    candidates = list(extract_path.rglob("pubspec.yaml"))
    if not candidates:
        raise FileNotFoundError(
            f"Extracted ZIP contains no pubspec.yaml under {extract_path}. "
            "Template generation may have failed."
        )
    # Pick the shallowest pubspec.yaml (the project root, not a sub-package).
    candidates.sort(key=lambda p: len(p.parts))
    return candidates[0].parent

_ANDROID_LICENSE_HASHES = {
    # Hashes written by `sdkmanager --licenses` when user accepts each license.
    "android-sdk-license": [
        "24333f8a63b6825ea9c5514f83c2829b004d1fee",
        "8933bad161af4178b1185d1a37fbf41ea5269c55",
        "d56f5187479451eabf01fb78af6dfcb131a6481e",
    ],
    "android-sdk-preview-license": [
        "84831b9409646a918e30573bab4c9c91346d8abd",
        "504667f4c0de7af1a06de9f4b1727b84351f2910",
    ],
    "android-ndk-license": [
        "592e0e6e5b9b49b0e6b6b0b5e8e5b9b49b0e6b6b",
    ],
    "android-ndk-sxs-license": [
        "04f7f4b1a2c676b90e2b7c70a82a0fefd05af7f2",
    ],
    "intel-android-extra-license": [
        "d975f751698a77b662f1254ddbeed3901e976f5a",
    ],
    "google-gdk-license": [
        "33b6a2b64607f11b759f320ef9dff4ae5c47d97a",
    ],
}

def _accept_android_licenses(android_home: str) -> None:
    """Pre-accept all Android SDK/NDK licenses by writing hash files."""
    import re
    licenses_dir = Path(android_home) / "licenses"
    licenses_dir.mkdir(parents=True, exist_ok=True)
    for filename, hashes in _ANDROID_LICENSE_HASHES.items():
        lic_file = licenses_dir / filename
        existing: set = set()
        if lic_file.exists():
            existing = {h.strip() for h in lic_file.read_text().splitlines() if h.strip()}
        all_hashes = existing | set(hashes)
        lic_file.write_text("\n".join(sorted(all_hashes)) + "\n")

def _fix_android_sdk_folders(android_home: str) -> None:
    """Rename PyInstaller's '-2' suffixed folders back to their correct names.

    PyInstaller appends '-2' (or '-3', etc.) to destination directory names
    when it detects a naming conflict during the bundle collection phase.
    This breaks Android SDK tools that expect exact directory names like
    'build-tools/34.0.0' or 'platforms/android-34'.
    """
    import re
    android_path = Path(android_home)
    for subdir in ("build-tools", "platforms", "platform-tools", "cmdline-tools"):
        parent = android_path / subdir
        if not parent.exists():
            continue
        for folder in sorted(parent.iterdir()):
            if not folder.is_dir():
                continue
            corrected = re.sub(r"-\d+$", "", folder.name)
            if corrected != folder.name:
                target = parent / corrected
                if not target.exists():
                    folder.rename(target)

def _force_delete_dir(path: Path, log_fn=None):
    """
    Reliably delete a directory on Windows, including long-path scenarios.
    """
    if not path.exists():
        return

    def _log(msg):
        if log_fn:
            log_fn(msg)

    gradlew = path / "android" / "gradlew.bat"
    if gradlew.exists():
        try:
            subprocess.run(
                [str(gradlew), "--stop"],
                cwd=str(path / "android"),
                capture_output=True,
                timeout=20,
            )
            time.sleep(2)
        except Exception:
            pass

    try:
        subprocess.run(["taskkill", "/F", "/IM", "java.exe"], capture_output=True)
        time.sleep(2)
    except Exception:
        pass

    abs_path = str(path.resolve())

    if os.name == "nt":
        try:
            with tempfile.TemporaryDirectory() as empty_dir:
                subprocess.run(
                    [
                        "robocopy", empty_dir, abs_path,
                        "/MIR", "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS",
                    ],
                    capture_output=True,
                    timeout=120,
                )
        except Exception as exc:
            _log(f"robocopy step failed (non-fatal): {exc}\n")

        try:
            subprocess.run(
                ["cmd", "/c", "rd", "/s", "/q", abs_path],
                capture_output=True,
                timeout=30,
            )
            time.sleep(1)
        except Exception:
            pass
    else:
        shutil.rmtree(path, ignore_errors=True)

    if path.exists():
        raise RuntimeError(
            f"Cannot delete stale build directory: {path}\n"
            "Please stop any running Gradle/Java processes, manually delete\n"
            f"  {path}\n"
            "then trigger a new build."
        )

@celery_app.task(bind=True, name="build_apk_task")
def build_apk_task(self, app_id: str):
    """Generate code and build APK for the given app_id."""
    db = _get_db()
    from app.queries import ProjectQueries
    
    try:
        rows = db.execute_query(ProjectQueries.GET_PROJECT_BY_ID, {"id": app_id})
        if not rows: return {"error": f"AppProject {app_id} not found"}
        app = dict(rows[0])

        _update_status(db, app_id, status="building", step="Initializing build...", log_append="--- Starting Build Process ---\n")

        # 1. Generate code
        _update_status(db, app_id, step="Generating Flutter project code...", log_append="Generating Flutter project templates...\n")
        
        all_model_assets = []
        model_asset_ids = app.get("model_asset_ids", [])
        if isinstance(model_asset_ids, str):
            model_asset_ids = json.loads(model_asset_ids)
            
        if model_asset_ids:
            placeholders = ", ".join([f":id{i}" for i in range(len(model_asset_ids))])
            query = f"SELECT * FROM model_assets WHERE id IN ({placeholders})"
            params = {f"id{i}": mid for i, mid in enumerate(model_asset_ids)}
            all_model_assets = db.execute_query(query, params)

        zip_bytes = generate_flutter_project(app, all_model_assets=all_model_assets)
        
        # 2. Extract to export directory
        _update_status(db, app_id, step="Preparing project workspace...", log_append="Cleaning previous build workspace...\n")
        export_root = Path(settings.exports_dir) / app_id

        _force_delete_dir(
            export_root,
            log_fn=lambda msg: _update_status(db, app_id, log_append=msg),
        )

        export_root.mkdir(parents=True, exist_ok=True)
        _update_status(db, app_id, step="Preparing project workspace...", log_append="Extracting project files...\n")
        _safe_extract(zip_bytes, export_root)

        # Locate the Flutter project root by finding pubspec.yaml.
        # Using next(iterdir()) was unreliable: OS-dependent ordering or any
        # extra file at the top level would pick the wrong directory and cause
        # every subsequent flutter command to fail with "No pubspec.yaml found".
        project_dir = _find_project_dir(export_root)
        _update_status(db, app_id, log_append=f"Project root: {project_dir.name}\n")
        
        # 3. Copy model assets
        if all_model_assets:
            _update_status(db, app_id, step="Embedding AI models...", log_append=f"Copying {len(all_model_assets)} models...\n")
            assets_dir = project_dir / "assets" / "models"
            assets_dir.mkdir(parents=True, exist_ok=True)
            
            for idx, ma in enumerate(all_model_assets):
                if ma.get("tflite_path"):
                    shutil.copy(ma["tflite_path"], assets_dir / f"model_{idx}.tflite")
                    
                    classes = ma.get("classes", [])
                    if isinstance(classes, str):
                        classes = json.loads(classes)
                    labels_content = "\n".join(classes or [])
                    (assets_dir / f"labels_{idx}.txt").write_text(labels_content)
        
        # 4. Run Flutter build
        # Paths are injected by the EXE launcher via env vars.
        # Fall back to legacy hardcoded locations for manual/dev usage.
        env = os.environ.copy()
        flutter_root  = env.get("FLUTTER_ROOT")  or r"C:\flutter"
        java_home     = env.get("JAVA_HOME")      or r"C:\jdk-17.0.14+7"
        android_home  = env.get("ANDROID_HOME")   or r"C:\android-sdk"

        flutter_path = os.path.join(flutter_root, "bin", "flutter.bat")
        if not os.path.exists(flutter_path):
            flutter_path = "flutter"

        dart_path = os.path.join(flutter_root, "bin", "dart.bat")
        if not os.path.exists(dart_path):
            dart_path = "dart"

        env["JAVA_HOME"]     = java_home
        env["ANDROID_HOME"]  = android_home
        env["FLUTTER_ROOT"]  = flutter_root
        env["PATH"] = (
            f"{java_home}\\bin;{flutter_root}\\bin;"
            f"{android_home}\\cmdline-tools\\latest\\bin;"
            f"{android_home}\\platform-tools;"
            f"{env.get('PATH', '')}"
        )

        # Fix PyInstaller folder rename issue and pre-accept SDK/NDK licenses.
        _fix_android_sdk_folders(android_home)
        _accept_android_licenses(android_home)

        # Prevent Gradle from spawning a separate daemon JVM process.
        # Without this, Gradle creates a daemon via CreateProcess with no console
        # flags; since its parent (java.exe) has no console, Windows allocates a
        # new visible console window for the daemon — causing blank terminal popups.
        # Disabling the daemon makes all Gradle work run in the initial JVM which
        # already inherits our no-console/SW_HIDE startup state.
        env["GRADLE_OPTS"] = (
            env.get("GRADLE_OPTS", "")
            + " -Dorg.gradle.daemon=false"
        ).strip()
        # Prevent any Java AWT/Swing windows from appearing during the build.
        env["JAVA_TOOL_OPTIONS"] = (
            env.get("JAVA_TOOL_OPTIONS", "")
            + " -Djava.awt.headless=true"
        ).strip()

        _update_status(db, app_id, step="Fetching dependencies...", log_append="Running 'flutter clean'...\n")
        _run_command_streaming(db, app_id, [flutter_path, "clean"], project_dir, env)

        _update_status(db, app_id, step="Fetching dependencies...", log_append="Running 'flutter pub get'...\n")
        ret_pub = _run_command_streaming(db, app_id, [flutter_path, "pub", "get"], project_dir, env)
        if ret_pub != 0: raise Exception(f"'flutter pub get' failed with exit code {ret_pub}. Check pubspec.yaml and network access.")
        _update_status(db, app_id, step="Generating database code...", log_append="\nRunning 'dart run build_runner build'...\n")
        ret_gen = _run_command_streaming(db, app_id, [dart_path, "run", "build_runner", "build", "--delete-conflicting-outputs"], project_dir, env)
        if ret_gen != 0: raise Exception(f"Code generation failed with exit code {ret_gen}")

        _update_status(db, app_id, step="Compiling APK...", log_append="\nRunning final compilation...\n")
        ret_build = _run_command_streaming(db, app_id, [
            flutter_path, "build", "apk", "--release", "--no-pub", "--android-skip-build-dependency-validation"
        ], project_dir, env)
        
        if ret_build != 0: raise Exception(f"Compilation failed with exit code {ret_build}")

        # 5. Locate APK
        apk_output = project_dir / "build" / "app" / "outputs" / "flutter-apk" / "app-release.apk"
        if not apk_output.exists():
            apk_output = project_dir / "build" / "app" / "outputs" / "apk" / "release" / "app-release.apk"

        if apk_output.exists():
            _update_status(db, app_id, status="ready", step="Success!", apk_path=str(apk_output), log_append="\n--- Build Finished ---")
            return {"status": "ready", "apk_path": str(apk_output)}
        else:
            raise FileNotFoundError("APK built but file not found on disk.")

    except Exception as e:
        error_msg = str(e)
        _update_status(db, app_id, status="error", step="Failed", error=error_msg, log_append=f"\nERROR: {error_msg}")
        return {"error": error_msg}
    finally:
        db.close()
