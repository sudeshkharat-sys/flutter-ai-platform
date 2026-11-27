import os
import subprocess
import zipfile
import io
import shutil
import json
import time
import tempfile
import hashlib
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
    # Keep Flutter/Gradle off the worker's console: on Windows they otherwise
    # share it, and a console signal / window event (Ctrl+C, Ctrl+Break, tab
    # closing) aimed at the build can take the Celery worker down with it.
    # No console + own process group + no stdin means the worker only ever
    # talks to the child through the output pipe.
    extra = {}
    if os.name == "nt":
        extra["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
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
        errors='replace',
        **extra,
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


# ---- Resume support -------------------------------------------------------
# A small file inside the export folder remembers which build steps finished.
# If the worker/backend crashes (or the build is stopped), the file stays on
# disk. The next build for the same app reads it and skips finished steps, and
# it does NOT run 'flutter clean', so the Gradle/pub cache is reused.
# The file is deleted when a build succeeds or fails normally, so only an
# interrupted build is ever resumed.
STATE_FILE_NAME = ".build_state.json"

def _app_fingerprint(app, models):
    """Short text that changes if the user edits the app after the crash."""
    keys = ["name", "package_name", "canvas_state", "app_settings",
            "inspection_tasks", "model_asset_ids"]
    data = {k: str(app.get(k)) for k in keys}
    data["models"] = [str(m.get("id")) + str(m.get("tflite_path")) for m in models]
    return hashlib.md5(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()

def _load_state(export_root):
    try:
        return json.loads((export_root / STATE_FILE_NAME).read_text(encoding="utf-8"))
    except Exception:
        return None

def _save_state(export_root, state):
    try:
        export_root.mkdir(parents=True, exist_ok=True)
        (export_root / STATE_FILE_NAME).write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass  # saving progress must never break the build

def _clear_state(export_root):
    try:
        (export_root / STATE_FILE_NAME).unlink()
    except Exception:
        pass

def _mark_done(export_root, state, step):
    if step not in state["done"]:
        state["done"].append(step)
    _save_state(export_root, state)

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

        export_root = Path(settings.exports_dir) / app_id
        fingerprint = _app_fingerprint(app, all_model_assets)

        # Resume only if an interrupted build left its progress file AND the
        # app was not edited since. Otherwise start fresh.
        state = _load_state(export_root)
        resuming = bool(
            state
            and state.get("fingerprint") == fingerprint
            and "generated" in state.get("done", [])
            and state.get("project_dir")
            and Path(state["project_dir"]).exists()
        )

        if resuming:
            project_dir = Path(state["project_dir"])
            done_text = ", ".join(state["done"])
            _update_status(db, app_id, step="Resuming interrupted build...",
                           log_append=f"\n--- RESUMING previous build (finished steps: {done_text}) ---\n")
        else:
            state = {"fingerprint": fingerprint, "done": [], "project_dir": ""}

            # Bump build_number so this build's versionCode is strictly higher
            # than the last -- otherwise Android can't tell the new APK is an
            # update and forces an uninstall (wiping the phone's paired-PC
            # setup) instead of updating in place.
            build_number_rows = db.execute_query(ProjectQueries.INCREMENT_BUILD_NUMBER, {"id": app_id})
            app["build_number"] = build_number_rows[0]["build_number"]

            zip_bytes = generate_flutter_project(app, all_model_assets=all_model_assets)
        
            # 2. Extract to export directory
            _update_status(db, app_id, step="Preparing project workspace...", log_append="Cleaning previous build workspace...\n")
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

                        # OCR bundles (model_kind "ocr_cnn"/"ocr_crnn") carry a
                        # charset/blank-index sidecar the CTC decoder needs at
                        # runtime -- copy it alongside the tflite when present.
                        if ma.get("charset_path") and os.path.exists(ma["charset_path"]):
                            shutil.copy(ma["charset_path"], assets_dir / f"charset_{idx}.txt")
                        if ma.get("meta_path") and os.path.exists(ma["meta_path"]):
                            shutil.copy(ma["meta_path"], assets_dir / f"meta_{idx}.json")
        
            state["project_dir"] = str(project_dir)
            _mark_done(export_root, state, "generated")

        # 4. Run Flutter build
        flutter_path = r"C:\flutter\bin\flutter.bat"
        if not os.path.exists(flutter_path): flutter_path = "flutter"

        env = os.environ.copy()
        env["JAVA_HOME"] = r"C:\jdk-17.0.14+7"
        env["ANDROID_HOME"] = r"C:\android-sdk"
        env["PATH"] = f"C:\\jdk-17.0.14+7\\bin;C:\\flutter\\bin;C:\\android-sdk\\cmdline-tools\\latest\\bin;C:\\android-sdk\\platform-tools;{env.get('PATH', '')}"
        env["FLUTTER_ROOT"] = "C:\\flutter"

        # 'flutter clean' wipes the cache, so run it only on a fresh build.
        if not resuming:
            _update_status(db, app_id, step="Fetching dependencies...", log_append="Running 'flutter clean'...\n")
            _run_command_streaming(db, app_id, [flutter_path, "clean"], project_dir, env)

        if "pub_get" in state["done"]:
            _update_status(db, app_id, log_append="Skipping 'flutter pub get' (already done).\n")
        else:
            _update_status(db, app_id, step="Fetching dependencies...", log_append="Running 'flutter pub get'...\n")
            ret_pub = _run_command_streaming(db, app_id, [flutter_path, "pub", "get"], project_dir, env)
            if ret_pub != 0: raise Exception(f"'flutter pub get' failed with exit code {ret_pub}. Check pubspec.yaml and network access.")
            _mark_done(export_root, state, "pub_get")

        dart_path = r"C:\flutter\bin\dart.bat"
        if not os.path.exists(dart_path): dart_path = "dart"
        if "build_runner" in state["done"]:
            _update_status(db, app_id, log_append="Skipping code generation (already done).\n")
        else:
            _update_status(db, app_id, step="Generating database code...", log_append="\nRunning 'dart run build_runner build'...\n")
            ret_gen = _run_command_streaming(db, app_id, [dart_path, "run", "build_runner", "build", "--delete-conflicting-outputs"], project_dir, env)
            if ret_gen != 0: raise Exception(f"Code generation failed with exit code {ret_gen}")
            _mark_done(export_root, state, "build_runner")

        # The compile step is the long one. Gradle keeps its own cache, so a
        # re-run after a crash continues from the cached work.
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
            _clear_state(export_root)
            _update_status(db, app_id, status="ready", step="Success!", apk_path=str(apk_output), log_append="\n--- Build Finished ---")
            return {"status": "ready", "apk_path": str(apk_output)}
        else:
            raise FileNotFoundError("APK built but file not found on disk.")

    except Exception as e:
        # A normal failure is not a crash: next build starts fresh.
        # (A crash/stop never reaches here, so its progress file stays.)
        try:
            _clear_state(Path(settings.exports_dir) / app_id)
        except Exception:
            pass
        error_msg = str(e)
        _update_status(db, app_id, status="error", step="Failed", error=error_msg, log_append=f"\nERROR: {error_msg}")
        return {"error": error_msg}
    finally:
        db.close()
