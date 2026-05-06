import os
import re
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
    new_error = error if error is not None else None # we might not have a specific column for error
    new_apk_path = apk_path if apk_path is not None else app.get("apk_path")
    
    current_log = app.get("build_log", "") or ""
    if log_append:
        if not isinstance(log_append, str):
            try: log_append = str(log_append)
            except: log_append = "[Encoding Error]"
        
        # Aggressively strip non-ASCII characters to prevent DB 'UntranslatableCharacter' errors
        # This replaces symbols like ✓ with a space.
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
        # Update every 2 seconds or 20 lines
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

def _force_delete_dir(path: Path, log_fn=None):
    """
    Reliably delete a directory on Windows, including long-path scenarios.

    Two compounding problems on Windows:
      1. shutil.rmtree(ignore_errors=True) silently fails when Gradle holds
         file locks → stale .flutter-plugins-dependencies survives → next build
         reuses the old plugin list (e.g. jni) → "package X does not exist".
      2. Gradle creates deeply nested intermediates such as:
           .../permission_handler_android/intermediates/javac/release/classes/
             com/baseflow/permissionhandler/PermissionManagerShouldShowRequest...
         These paths exceed Windows MAX_PATH (260 chars).  Both shutil.rmtree
         and 'rd /s /q' fail with [WinError 3] on such paths.

    Strategy (Windows):
      1. Stop Gradle daemon via 'gradlew --stop' to release build-dir locks.
      2. Kill remaining java.exe and wait for handles to close.
      3. Use robocopy /MIR to mirror an empty temp dir over the target —
         robocopy uses the \\?\ long-path prefix internally, so it can empty
         directories that exceed MAX_PATH where rmtree/rd cannot.
      4. Now that the directory is empty, 'rd /s /q' removes the shell.
      5. Raise clearly (not silently) if the directory still exists.
    """
    if not path.exists():
        return

    def _log(msg):
        if log_fn:
            log_fn(msg)

    # 1. Ask the Gradle daemon to stop so it releases its locks.
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

    # 2. Kill java.exe (covers any daemon that ignored --stop).
    try:
        subprocess.run(["taskkill", "/F", "/IM", "java.exe"], capture_output=True)
        time.sleep(2)  # Give Windows time to release file handles
    except Exception:
        pass

    abs_path = str(path.resolve())

    if os.name == "nt":
        # 3. robocopy /MIR: mirror an empty directory over the target.
        #    This is the standard Windows trick for deleting trees with paths
        #    longer than MAX_PATH — robocopy handles \\?\ prefixes internally.
        try:
            with tempfile.TemporaryDirectory() as empty_dir:
                subprocess.run(
                    [
                        "robocopy", empty_dir, abs_path,
                        "/MIR",   # mirror (delete everything in target)
                        "/NFL",   # no file list
                        "/NDL",   # no directory list
                        "/NJH",   # no job header
                        "/NJS",   # no job summary
                        "/NC",    # no class
                        "/NS",    # no size
                    ],
                    capture_output=True,
                    timeout=120,
                )
        except Exception as exc:
            _log(f"robocopy step failed (non-fatal): {exc}\n")

        # 4. Remove the now-empty directory shell.
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
        # Non-Windows: plain rmtree is fine.
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

        # Force-delete the old directory so no stale files survive.
        # shutil.rmtree(ignore_errors=True) silently fails on Windows when
        # Gradle holds file locks, leaving old .flutter-plugins-dependencies
        # and Gradle caches that cause "package jni does not exist" errors.
        _force_delete_dir(
            export_root,
            log_fn=lambda msg: _update_status(db, app_id, log_append=msg),
        )

        export_root.mkdir(parents=True, exist_ok=True)
        _update_status(db, app_id, step="Preparing project workspace...", log_append="Extracting project files...\n")
        _safe_extract(zip_bytes, export_root)

        # Derive project_dir from the known app name slug — same formula as
        # generator.py — instead of next(iterdir()) which is unreliable when a
        # stale directory from a failed previous cleanup survives and sorts first.
        _app_name_slug = re.sub(r'[^a-z0-9_]', '_', app.get("name", "My App").lower()).strip('_') or "app"
        project_dir = export_root / _app_name_slug
        _update_status(db, app_id, log_append=f"Project dir: {project_dir}\n")

        if not (project_dir / "pubspec.yaml").exists():
            found = [str(p) for p in export_root.rglob("pubspec.yaml")]
            raise FileNotFoundError(
                f"pubspec.yaml not found in {project_dir}. "
                f"Searched export root — found at: {found or 'nowhere'}"
            )
        
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
        flutter_path = r"C:\flutter\bin\flutter.bat"
        if not os.path.exists(flutter_path): flutter_path = "flutter"

        env = os.environ.copy()
        env["JAVA_HOME"] = r"C:\jdk-17.0.14+7"
        env["ANDROID_HOME"] = r"C:\android-sdk"
        env["PATH"] = f"C:\\jdk-17.0.14+7\\bin;C:\\flutter\\bin;C:\\android-sdk\\cmdline-tools\\latest\\bin;C:\\android-sdk\\platform-tools;{env.get('PATH', '')}"
        env["FLUTTER_ROOT"] = "C:\\flutter"
        # Resolve pub cache to the current user's LOCALAPPDATA directory.
        # Falling back to a hardcoded username is fragile; LOCALAPPDATA always
        # points to the profile of whoever is running the process.
        local_app_data = env.get("LOCALAPPDATA", r"C:\Users\50017162\AppData\Local")
        env.setdefault("PUB_CACHE", os.path.join(local_app_data, "Pub", "Cache"))

        # flutter clean — wipe any Gradle / Dart build caches that may have
        # survived an incomplete previous cleanup (e.g. cross-drive .gradle dirs).
        # This is fast on a freshly extracted project but is a critical safety net.
        _update_status(db, app_id, step="Fetching dependencies...", log_append="Running 'flutter clean'...\n")
        _run_command_streaming(db, app_id, [flutter_path, "clean"], project_dir, env)

        # flutter pub get — must succeed to generate .flutter-plugins-dependencies
        # (which Gradle reads to produce GeneratedPluginRegistrant.java).
        # If it fails silently the subsequent Gradle build will reference packages
        # that don't exist in the compile classpath → "package X does not exist".
        _update_status(db, app_id, step="Fetching dependencies...", log_append="Running 'flutter pub get'...\n")
        ret_pub = _run_command_streaming(db, app_id, [flutter_path, "pub", "get"], project_dir, env)
        if ret_pub != 0: raise Exception(f"'flutter pub get' failed with exit code {ret_pub}. Check pubspec.yaml and network access.")

        # dart run build_runner
        dart_path = r"C:\flutter\bin\dart.bat"
        if not os.path.exists(dart_path): dart_path = "dart"
        _update_status(db, app_id, step="Generating database code...", log_append="\nRunning 'dart run build_runner build'...\n")
        ret_gen = _run_command_streaming(db, app_id, [dart_path, "run", "build_runner", "build", "--delete-conflicting-outputs"], project_dir, env)
        if ret_gen != 0: raise Exception(f"Code generation failed with exit code {ret_gen}")

        # flutter build apk
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
