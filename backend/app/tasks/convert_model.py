import datetime
import sys
from pathlib import Path
from app.tasks.celery_app import celery_app
from app.config import settings


def _tlog(msg: str) -> None:
    """Write a timestamped message directly to stderr (visible in launcher.log
    even when sys.stdout is temporarily redirected to a StringIO for log capture)."""
    try:
        line = f"[{datetime.datetime.now():%H:%M:%S.%f}] [convert_model] {msg}\n"
        # sys.stderr is NOT redirected by the task — always goes to launcher.log
        print(line, end="", file=sys.stderr, flush=True)
    except Exception:
        pass


def _get_db():
    from app.connectors.state_db import StateDBConnector
    return StateDBConnector()

def _update_asset(db, asset_id, **kwargs):
    from app.queries import ModelAssetQueries
    import json

    # Get current
    rows = db.execute_query(ModelAssetQueries.GET_MODEL_BY_ID, {"id": asset_id})
    if not rows: return
    asset = dict(rows[0])

    for k, v in kwargs.items():
        asset[k] = v

    query = """
        UPDATE model_assets
        SET status = :status, error_message = :error_message, conversion_log = :conversion_log,
            tflite_path = :tflite_path, labels_path = :labels_path
        WHERE id = :id
    """

    db.execute_update(query, {
        "id": asset_id,
        "status": asset.get("status"),
        "error_message": asset.get("error_message"),
        "conversion_log": asset.get("conversion_log"),
        "tflite_path": asset.get("tflite_path"),
        "labels_path": asset.get("labels_path")
    })

@celery_app.task(bind=True, name="convert_model_to_tflite")
def convert_model_to_tflite(self, model_asset_id: str):
    """Convert an uploaded .pt file to .tflite using Ultralytics."""
    _tlog(f"Task received for {model_asset_id}")
    db = None
    try:
        db = _get_db()
        _tlog(f"DB connected: {db.settings.postgres_url}")
    except Exception as exc:
        _tlog(f"FATAL: could not connect to DB: {exc}")
        raise

    from app.queries import ModelAssetQueries
    import json

    try:
        rows = db.execute_query(ModelAssetQueries.GET_MODEL_BY_ID, {"id": model_asset_id})
        if not rows:
            _tlog(f"ModelAsset {model_asset_id} not found in DB.")
            return {"error": f"ModelAsset {model_asset_id} not found"}

        asset = dict(rows[0])
        _tlog(f"Processing: {asset['vision_project_name']} (status={asset['status']})")
        pt_path = Path(asset["pt_path"])
        if not pt_path.exists():
            _tlog(f"pt_path missing: {pt_path}")
            _update_asset(db, model_asset_id, status="error", error_message=f"Uploaded model file not found at {pt_path}.")
            return {"error": f"Uploaded model file not found at {pt_path}."}

        # ── Convert to TFLite ─────────────────────────────────────────────────
        _tlog("Setting status=converting")
        _update_asset(db, model_asset_id, status="converting", conversion_log="Starting model conversion pipeline...\n")

        from ultralytics import YOLO
        import ultralytics.utils as _uu
        import ultralytics.utils.checks as _uc
        _uu.AUTOINSTALL = False                        # module-level flag
        _uc.check_requirements = lambda *a, **kw: None # belt-and-suspenders
        import torch
        import os
        import io

        # Redirect stdout to capture Ultralytics' verbose output for the UI log.
        # Note: sys.stderr is intentionally NOT redirected so _tlog() messages
        # continue to appear in launcher.log throughout the conversion.
        log_capture = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = log_capture

        try:
            os.environ["ULTRALYTICS_AUTOUPDATE"]              = "False"
            os.environ["YOLO_AUTOINSTALL"]                    = "false"
            os.environ["ULTRALYTICS_SKIP_REQUIREMENTS_CHECKS"] = "1"

            log_txt = asset.get("conversion_log", "")
            log_txt += f"Device: {'cuda' if torch.cuda.is_available() else 'cpu'}\n"
            log_txt += f"Input Size: {asset.get('input_size', 640)}x{asset.get('input_size', 640)}\n"
            log_txt += "Step 1: Exporting PyTorch to TFLite format (this may take a few minutes)...\n"
            _update_asset(db, model_asset_id, conversion_log=log_txt)

            _tlog("Loading YOLO model...")
            model = YOLO(str(pt_path))
            model_dir = pt_path.parent
            imgsz = asset.get("input_size", 640)

            _tlog(f"Calling model.export(format=tflite, imgsz={imgsz})...")
            model.export(format="tflite", imgsz=imgsz, int8=False)
            _tlog("model.export() returned.")

            # Sync captured stdout into the conversion log
            log_txt += log_capture.getvalue()
            _update_asset(db, model_asset_id, conversion_log=log_txt)

            # Find the exported .tflite file
            tflite_path = None
            for p in model_dir.rglob("*.tflite"):
                if "float32" in p.name or p.name == "model.tflite" or p.name.endswith(".tflite"):
                    tflite_path = p
                    break

            if not tflite_path or not tflite_path.exists():
                raise FileNotFoundError(f"TFLite export produced no usable output file in {model_dir}")

            log_txt += f"\nFound TFLite at: {tflite_path.name}\n"
            log_txt += "Step 2: Generating labels.txt...\n"

            labels_path = model_dir / "labels.txt"
            classes = asset.get("classes", [])
            if isinstance(classes, str):
                classes = json.loads(classes)

            labels_path.write_text("\n".join(classes or []))

            log_txt += "Conversion successful! Ready for bundling.\n"

            _update_asset(db, model_asset_id,
                tflite_path=str(tflite_path),
                labels_path=str(labels_path),
                status="ready",
                conversion_log=log_txt
            )
            _tlog(f"Done — status=ready, tflite={tflite_path.name}")

        finally:
            sys.stdout = old_stdout

        return {"status": "ready", "tflite_path": str(tflite_path)}

    except Exception as exc:
        _tlog(f"Task FAILED: {exc}")
        if db is not None:
            try:
                _update_asset(db, model_asset_id, status="error", error_message=str(exc)[:500])
            except Exception as inner:
                _tlog(f"Could not write error status: {inner}")
        raise
    finally:
        if db is not None:
            db.close()
