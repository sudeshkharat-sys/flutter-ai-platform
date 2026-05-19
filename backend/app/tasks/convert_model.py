from pathlib import Path
from app.tasks.celery_app import celery_app
from app.config import settings

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
    db = _get_db()
    from app.queries import ModelAssetQueries
    import json
    
    print(f"DEBUG TASK: Starting conversion for {model_asset_id}")
    print(f"DEBUG TASK: DB URL = {db.settings.postgres_url}")
    
    try:
        # Check if record exists
        rows = db.execute_query(ModelAssetQueries.GET_MODEL_BY_ID, {"id": model_asset_id})
        if not rows:
            print(f"DEBUG TASK: Record {model_asset_id} NOT FOUND in database.")
            # List some IDs to see what's in there
            all_ids = db.execute_query("SELECT id FROM model_assets LIMIT 5")
            print(f"DEBUG TASK: Existing IDs in model_assets: {all_ids}")
            return {"error": f"ModelAsset {model_asset_id} not found"}
        
        asset = dict(rows[0])
        print(f"DEBUG TASK: Found asset: {asset['vision_project_name']}")
        pt_path = Path(asset["pt_path"])
        if not pt_path.exists():
            _update_asset(db, model_asset_id, status="error", error_message=f"Uploaded model file not found at {pt_path}.")
            return {"error": f"Uploaded model file not found at {pt_path}."}

        # ── Convert to TFLite ─────────────────────────────────────────────────
        _update_asset(db, model_asset_id, status="converting", conversion_log="Starting model conversion pipeline...\n")

        from ultralytics import YOLO
        import torch
        import os
        import sys
        import io

        # Redirect stdout to capture logs
        log_capture = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = log_capture

        try:
            # ── Fix: Disable AutoUpdate and requirement checks ───────────────────
            os.environ["ULTRALYTICS_AUTOUPDATE"] = "False"
            
            log_txt = asset.get("conversion_log", "")
            log_txt += f"Device: {'cuda' if torch.cuda.is_available() else 'cpu'}\n"
            log_txt += f"Input Size: {asset.get('input_size', 640)}x{asset.get('input_size', 640)}\n"
            log_txt += "Step 1: Exporting PyTorch to TFLite format (this may take a few minutes)...\n"
            _update_asset(db, model_asset_id, conversion_log=log_txt)

            model = YOLO(str(pt_path))
            model_dir = pt_path.parent
            imgsz = asset.get("input_size", 640)

            # Step 1: Export to ONNX first (more reliable than direct tflite export)
            log_txt += "Step 1a: Exporting to ONNX intermediate format...\n"
            _update_asset(db, model_asset_id, conversion_log=log_txt)
            onnx_result = model.export(format="onnx", imgsz=imgsz, opset=11, simplify=False)
            onnx_path = Path(str(onnx_result))

            # Step 2: Convert ONNX → TFLite via onnx2tf with YOLO11-safe params
            log_txt += "Step 1b: Converting ONNX to TFLite...\n"
            _update_asset(db, model_asset_id, conversion_log=log_txt)

            # Patch onnx2tf accuracy correction which crashes on YOLO11 scalar constants
            try:
                import onnx2tf.utils.common_functions as _onnx2tf_cf
                _orig_correction = _onnx2tf_cf.correction_process_for_accuracy_errors
                def _safe_correction(*args, **kwargs):
                    try:
                        return _orig_correction(*args, **kwargs)
                    except Exception:
                        pass
                _onnx2tf_cf.correction_process_for_accuracy_errors = _safe_correction
            except Exception:
                pass

            import onnx2tf
            tflite_out_dir = model_dir / "tflite_output"
            tflite_out_dir.mkdir(exist_ok=True)
            onnx2tf.convert(
                input_onnx_file_path=str(onnx_path),
                output_folder_path=str(tflite_out_dir),
                non_verbose=True,
                not_use_onnxsim=True,
            )
            
            # Sync logs
            log_txt += log_capture.getvalue()
            _update_asset(db, model_asset_id, conversion_log=log_txt)

            # Step 2: Find the exported .tflite file
            tflite_path = None
            for p in tflite_out_dir.rglob("*.tflite"):
                if "float32" in p.name or p.name == "model.tflite" or p.name.endswith(".tflite"):
                    tflite_path = p
                    break
            # Fallback: search whole model_dir
            if not tflite_path:
                for p in model_dir.rglob("*.tflite"):
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
            
            # ── Mark ready ───────────────────────────────────────────────────────
            _update_asset(db, model_asset_id, 
                tflite_path=str(tflite_path), 
                labels_path=str(labels_path),
                status="ready",
                conversion_log=log_txt
            )

        finally:
            sys.stdout = old_stdout

        return {"status": "ready", "tflite_path": str(tflite_path)}

    except Exception as exc:
        _update_asset(db, model_asset_id, status="error", error_message=str(exc)[:500])
        raise
    finally:
        db.close()
