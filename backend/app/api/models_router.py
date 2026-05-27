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
    Extract YOLO class names from a .pt checkpoint without requiring
    ultralytics to be importable at all.

    Strategy
    --------
    1. Safe-unpickle: read data.pkl directly from the PyTorch zip archive
       using a custom Unpickler that:
         - stubs out all tensor reconstruction so the load never crashes on
           missing CUDA/storage/ultralytics classes
         - replaces any unimportable class with _Placeholder, which copies
           the object's __dict__ (preserving the 'names' attr)
    2. Full torch.load (weights_only=False) — fallback if step 1 finds nothing
    3. YOLO() constructor — last resort

    All three stages are wrapped in try/except so this function NEVER throws.
    """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _names_to_list(names) -> list[str]:
        if isinstance(names, dict):
            return [names[i] for i in sorted(names.keys())]
        if isinstance(names, (list, tuple)):
            return list(names)
        return []

    def _find_names(obj, depth: int = 0) -> list[str]:
        """Recursively search obj for a YOLO 'names' mapping."""
        if depth > 6:
            return []
        # Direct dict key or instance attribute
        names_val = None
        if isinstance(obj, dict):
            names_val = obj.get("names")
        if names_val is None and hasattr(obj, "__dict__"):
            names_val = obj.__dict__.get("names")
        if names_val is not None:
            r = _names_to_list(names_val)
            if r:
                return r
        # Recurse into known sub-keys that hold the model object
        for key in ("model", "ema", "train_args", "module"):
            child = None
            if isinstance(obj, dict):
                child = obj.get(key)
            elif hasattr(obj, "__dict__"):
                child = obj.__dict__.get(key)
            if child is not None:
                r = _find_names(child, depth + 1)
                if r:
                    return r
        return []

    # ------------------------------------------------------------------
    # Safe-unpickle infrastructure
    # ------------------------------------------------------------------

    class _Placeholder:
        """Stand-in for any class we can't (or don't want to) import."""
        def __new__(cls, *a, **kw):
            return object.__new__(cls)
        def __init__(self, *a, **kw):
            pass
        def __setstate__(self, state):
            # Copies ALL attributes including 'names'
            if isinstance(state, dict):
                self.__dict__.update(state)

    # Functions that reconstruct tensors - we stub them to return None so
    # tensor weights don't cause errors (we only care about 'names').
    _TENSOR_FUNCS = {
        ("torch._utils",   "_rebuild_tensor_v2"),
        ("torch._utils",   "_rebuild_tensor"),
        ("torch._utils",   "_rebuild_parameter"),
        ("torch._tensor",  "_rebuild_from_type_v2"),
        ("torch.storage",  "_load_from_bytes"),
    }

    def _tensor_stub(*args, **kwargs):
        """Replaces tensor/parameter reconstruction - returns None."""
        return None

    class _SafeUnpickler(pickle.Unpickler):
        def find_class(self, module, name):
            # Stub tensor reconstruction functions
            if (module, name) in _TENSOR_FUNCS:
                return _tensor_stub
            # Stub all torch Storage classes (FloatStorage, etc.)
            if module in ("torch", "torch.storage") and "Storage" in name:
                return type(f"_PH_{name}", (_Placeholder,), {})
            # Try normal import first
            try:
                return super().find_class(module, name)
            except Exception:
                # Replace any unimportable class with a unique _Placeholder
                return type(f"_PH_{name}", (_Placeholder,), {})

        def persistent_load(self, pid):
            # Tensor storages are referenced via persistent IDs in the zip.
            # We don't need the actual tensor data — return None so
            # _tensor_stub above accepts it without crashing.
            return None

    # ------------------------------------------------------------------
    # Method 1 — safe-unpickle (no ultralytics needed)
    # ------------------------------------------------------------------
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
                    print("[classes] safe-unpickle: loaded OK but no 'names' found")
                else:
                    print("[classes] safe-unpickle: no data.pkl entry in zip")
        else:
            print("[classes] file is not a zip archive")
    except Exception as e:
        print(f"[classes] safe-unpickle error: {e}")
        traceback.print_exc()

    # ------------------------------------------------------------------
    # Method 2 — full torch.load (requires ultralytics importable)
    # ------------------------------------------------------------------
    try:
        import torch
        ckpt = torch.load(pt_path, map_location="cpu", weights_only=False)
        result = _find_names(ckpt)
        if result:
            print(f"[classes] Found {len(result)} classes via torch.load")
            return result
        print("[classes] torch.load: no 'names' found")
    except Exception as e:
        print(f"[classes] torch.load error: {e}")

    # ------------------------------------------------------------------
    # Method 3 — YOLO() constructor (last resort, never throws)
    # ------------------------------------------------------------------
    try:
        from ultralytics import YOLO
        yolo = YOLO(pt_path)
        if hasattr(yolo, "names") and yolo.names:
            result = _names_to_list(yolo.names)
            print(f"[classes] Found {len(result)} classes via YOLO()")
            return result
    except Exception as e:
        print(f"[classes] YOLO fallback error: {e}")

    print("[classes] All methods failed, returning []")
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


@router.get("/debug")
def debug_status(db: StateDBConnector = Depends(get_db_connector)):
    """Quick health check: DB connection + models_dir write access."""
    result = {"db": False, "models_dir": str(settings.models_dir), "models_dir_writable": False}
    try:
        rows = db.execute_query("SELECT 1 AS ok")
        result["db"] = bool(rows)
    except Exception as e:
        result["db_error"] = str(e)
    try:
        settings.models_dir.mkdir(parents=True, exist_ok=True)
        test_file = settings.models_dir / ".write_test"
        test_file.write_text("ok")
        test_file.unlink()
        result["models_dir_writable"] = True
    except Exception as e:
        result["models_dir_error"] = str(e)
    return result


@router.post("/upload", response_model=ModelAssetResponse)
def upload_model(
    file: UploadFile = File(...),
    model_name: str = Form(...),
    classes: str = Form(None),
    input_size: int = Form(640),
    db: StateDBConnector = Depends(get_db_connector),
):
    print(f"[upload] START file={file.filename} model_name={model_name}")

    if not file.filename.endswith(".pt"):
        raise HTTPException(status_code=422, detail="Only .pt model files are supported.")

    # Parse classes sent from frontend (JSON array string)
    class_list: list = []
    if classes:
        try:
            parsed = json.loads(classes)
            if isinstance(parsed, list):
                class_list = parsed
        except Exception:
            raise HTTPException(status_code=422, detail="classes must be a JSON array.")

    # Save uploaded file
    asset_id = str(uuid.uuid4())
    model_dir = settings.models_dir / asset_id
    try:
        model_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[upload] mkdir FAILED: {e}")
        raise HTTPException(status_code=500, detail=f"Cannot create model directory: {e}")

    pt_path = model_dir / "model.pt"
    try:
        with open(pt_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        size = pt_path.stat().st_size
        print(f"[upload] File saved to {pt_path} ({size} bytes)")
    except Exception as e:
        print(f"[upload] File save FAILED: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"File save error: {e}")

    # Extract classes if not supplied by the frontend
    if not class_list:
        class_list = extract_classes_from_pt(str(pt_path))
    print(f"[upload] classes ({len(class_list)}): {class_list}")

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

    # DB insert
    try:
        print("[upload] Inserting into DB...")
        db.execute_insert(ModelAssetQueries.INSERT_MODEL, params)
        print("[upload] DB insert OK")
    except Exception as e:
        print(f"[upload] DB insert FAILED: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Database insert error: {e}")

    # Queue Celery task (non-fatal if it fails)
    try:
        convert_model_to_tflite.delay(asset_id)
        print(f"[upload] Celery task queued for {asset_id}")
    except Exception as e:
        print(f"[upload] Celery queue warning (non-fatal): {e}")

    # Return the newly created record
    try:
        rows = db.execute_query(ModelAssetQueries.GET_MODEL_BY_ID, {"id": asset_id})
        if not rows:
            raise HTTPException(status_code=500, detail="Upload succeeded but record not found")
        print(f"[upload] SUCCESS asset_id={asset_id}")
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
