import io
import json
import pickle
import shutil
import traceback
import zipfile
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from app.connectors.state_db import StateDBConnector
from app.queries import ModelAssetQueries
from app.schemas.base import ModelAssetResponse, ModelAssetStatus
from app.tasks.convert_model import convert_model_to_tflite
from app.config import settings
import uuid

router = APIRouter(prefix="/models", tags=["models"])

def get_db_connector():
    connector = StateDBConnector()
    try:
        yield connector
    finally:
        pass

@router.get("", response_model=list[ModelAssetResponse])
def list_models(db: StateDBConnector = Depends(get_db_connector)):
    rows = db.execute_query(ModelAssetQueries.GET_ALL_MODELS)
    return rows


def extract_classes_from_pt(pt_path: str) -> list[str]:
    """
    Extract YOLO class names from a .pt checkpoint.

    Three-stage fallback:
      1. Safe-unpickle: reads data.pkl directly from the zip archive using a
         custom Unpickler that substitutes _Placeholder for any class it
         cannot import.  Works even when ultralytics is not fully importable
         inside a PyInstaller bundle.
      2. torch.load (weights_only=False): standard full load.
      3. YOLO() constructor — last resort.
    """

    # ------------------------------------------------------------------ #
    # Helpers shared by all three methods
    # ------------------------------------------------------------------ #

    def _names_to_list(names) -> list[str]:
        if isinstance(names, dict):
            return [names[i] for i in sorted(names.keys())]
        if isinstance(names, (list, tuple)):
            return list(names)
        return []

    def _find_names(obj, depth: int = 0) -> list[str]:
        """Recursively search obj for a 'names' dict/list."""
        if depth > 6:
            return []
        names_val = None
        if isinstance(obj, dict):
            names_val = obj.get("names")
        if names_val is None and hasattr(obj, "__dict__"):
            names_val = obj.__dict__.get("names")
        if names_val is not None:
            result = _names_to_list(names_val)
            if result:
                return result
        # Recurse into known sub-keys
        for key in ("model", "ema", "train_args", "module"):
            child = None
            if isinstance(obj, dict):
                child = obj.get(key)
            elif hasattr(obj, "__dict__"):
                child = obj.__dict__.get(key)
            if child is not None:
                result = _find_names(child, depth + 1)
                if result:
                    return result
        return []

    # ------------------------------------------------------------------ #
    # Safe-unpickle infrastructure
    # ------------------------------------------------------------------ #

    class _Placeholder:
        """Stand-in for any class that can't be imported during unpickling."""
        def __new__(cls, *a, **kw):
            return object.__new__(cls)
        def __init__(self, *a, **kw):
            pass
        def __setstate__(self, state):
            if isinstance(state, dict):
                self.__dict__.update(state)

    class _SafeUnpickler(pickle.Unpickler):
        def find_class(self, module, name):
            try:
                return super().find_class(module, name)
            except Exception:
                # Unique class per type so __dict__ isn't accidentally shared
                return type(f"_PH_{name}", (_Placeholder,), {})
        def persistent_load(self, pid):
            # Tensors live in separate zip entries; we don't need them
            return None

    # ------------------------------------------------------------------ #
    # Method 1 — safe unpickle directly from zip (no ultralytics needed)
    # ------------------------------------------------------------------ #
    try:
        if zipfile.is_zipfile(pt_path):
            with zipfile.ZipFile(pt_path, "r") as zf:
                pkl_entries = [n for n in zf.namelist() if n.endswith("data.pkl")]
                if pkl_entries:
                    with zf.open(pkl_entries[0]) as fh:
                        raw = fh.read()
                    ckpt = _SafeUnpickler(io.BytesIO(raw)).load()
                    result = _find_names(ckpt)
                    if result:
                        print(f"[classes] Found {len(result)} classes via safe-unpickle")
                        return result
                    print("[classes] safe-unpickle: loaded but no names found")
                else:
                    print("[classes] safe-unpickle: no data.pkl entry in zip")
        else:
            print("[classes] not a zip archive, skipping safe-unpickle")
    except Exception as e:
        print(f"[classes] safe-unpickle error: {e}")
        traceback.print_exc()

    # ------------------------------------------------------------------ #
    # Method 2 — full torch.load (requires ultralytics classes importable)
    # ------------------------------------------------------------------ #
    try:
        import torch
        ckpt = torch.load(pt_path, map_location="cpu", weights_only=False)
        result = _find_names(ckpt)
        if result:
            print(f"[classes] Found {len(result)} classes via torch.load")
            return result
        print("[classes] torch.load: no names found")
    except Exception as e:
        print(f"[classes] torch.load error: {e}")

    # ------------------------------------------------------------------ #
    # Method 3 — YOLO() constructor (last resort)
    # ------------------------------------------------------------------ #
    try:
        from ultralytics import YOLO
        yolo = YOLO(pt_path)
        if hasattr(yolo, "names") and yolo.names:
            result = _names_to_list(yolo.names)
            print(f"[classes] Found {len(result)} classes via YOLO()")
            return result
    except Exception as e:
        print(f"[classes] YOLO fallback error: {e}")

    print("[classes] Could not extract classes, returning []")
    return []


@router.post("/extract-classes")
def extract_classes_from_file(file: UploadFile = File(...)):
    if not file.filename.endswith(".pt"):
        raise HTTPException(status_code=422, detail="Only .pt model files are supported.")

    temp_id = str(uuid.uuid4())
    temp_path = settings.models_dir / f"temp_{temp_id}.pt"
    settings.models_dir.mkdir(parents=True, exist_ok=True)

    try:
        with open(temp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        classes = extract_classes_from_pt(str(temp_path))
        return {"classes": classes}
    finally:
        if temp_path.exists():
            temp_path.unlink()


@router.post("/{model_asset_id}/detect-classes")
def detect_model_classes(model_asset_id: str, db: StateDBConnector = Depends(get_db_connector)):
    rows = db.execute_query(ModelAssetQueries.GET_MODEL_BY_ID, {"id": model_asset_id})
    if not rows:
        raise HTTPException(status_code=404, detail="Model asset or .pt file not found")
    asset = rows[0]
    if not asset["pt_path"]:
        raise HTTPException(status_code=404, detail="Model asset missing pt_path")
    classes = extract_classes_from_pt(asset["pt_path"])
    return {"classes": classes}


@router.post("/upload", response_model=ModelAssetResponse)
def upload_model(
    file: UploadFile = File(...),
    model_name: str = Form(...),
    classes: str = Form(None),
    input_size: int = Form(640),
    db: StateDBConnector = Depends(get_db_connector),
):
    print(f"[upload] Starting upload: {file.filename}, model_name={model_name}")

    if not file.filename.endswith(".pt"):
        raise HTTPException(status_code=422, detail="Only .pt model files are supported.")

    class_list = []
    if classes:
        try:
            parsed = json.loads(classes)
            if isinstance(parsed, list):
                class_list = parsed
        except Exception:
            raise HTTPException(status_code=422, detail="classes must be a JSON array.")

    asset_id = str(uuid.uuid4())
    model_dir = settings.models_dir / asset_id
    model_dir.mkdir(parents=True, exist_ok=True)
    pt_path = model_dir / "model.pt"
    print(f"[upload] Saving file to {pt_path}")

    try:
        with open(pt_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        print(f"[upload] File saved ({pt_path.stat().st_size} bytes)")
    except Exception as e:
        print(f"[upload] File save FAILED: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"File save error: {e}")

    if not class_list:
        class_list = extract_classes_from_pt(str(pt_path))
    print(f"[upload] Classes ({len(class_list)}): {class_list}")

    params = {
        "id": asset_id,
        "vision_project_id": asset_id,
        "vision_project_name": model_name,
        "model_type": "uploaded",
        "classes": json.dumps(class_list),
        "pt_path": str(pt_path),
        "tflite_path": None,
        "labels_path": None,
        "status": "pending",
        "error_message": None,
        "conversion_log": "",
        "input_size": input_size,
        "vision_platform_url": "",
        "vision_platform_token": ""
    }

    try:
        print(f"[upload] Inserting into DB...")
        db.execute_insert(ModelAssetQueries.INSERT_MODEL, params)
        print(f"[upload] DB insert OK")
    except Exception as e:
        print(f"[upload] DB insert FAILED: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    try:
        convert_model_to_tflite.delay(asset_id)
        print(f"[upload] Celery task queued for {asset_id}")
    except Exception as e:
        print(f"[upload] Celery queue warning: {e}")

    try:
        rows = db.execute_query(ModelAssetQueries.GET_MODEL_BY_ID, {"id": asset_id})
        if not rows:
            raise HTTPException(status_code=500, detail="Upload succeeded but model not found in DB")
        print(f"[upload] Upload complete for {asset_id}")
        return rows[0]
    except HTTPException:
        raise
    except Exception as e:
        print(f"[upload] Final query FAILED: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Post-insert query error: {e}")


@router.get("/{model_asset_id}", response_model=ModelAssetResponse)
def get_model(model_asset_id: str, db: StateDBConnector = Depends(get_db_connector)):
    rows = db.execute_query(ModelAssetQueries.GET_MODEL_BY_ID, {"id": model_asset_id})
    if not rows:
        raise HTTPException(status_code=404, detail="Model asset not found")
    return rows[0]


@router.get("/{model_asset_id}/status", response_model=ModelAssetStatus)
def get_model_status(model_asset_id: str, db: StateDBConnector = Depends(get_db_connector)):
    rows = db.execute_query(ModelAssetQueries.GET_MODEL_BY_ID, {"id": model_asset_id})
    if not rows:
        raise HTTPException(status_code=404, detail="Model asset not found")
    asset = rows[0]
    return ModelAssetStatus(
        id=asset["id"],
        status=asset["status"],
        error_message=asset["error_message"],
        tflite_path=asset["tflite_path"],
    )


@router.delete("/{model_asset_id}")
def delete_model(model_asset_id: str, db: StateDBConnector = Depends(get_db_connector)):
    deleted = db.execute_update(ModelAssetQueries.DELETE_MODEL, {"id": model_asset_id})
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Model asset not found")
    return {"deleted": True}
