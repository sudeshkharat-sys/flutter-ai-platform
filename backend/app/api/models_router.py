import json
import shutil
import traceback
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
    Extract class names from a YOLO .pt checkpoint.
    Uses torch.load directly — works in PyInstaller EXE without full YOLO init.
    """
    import torch

    def _names_to_list(names) -> list[str]:
        if isinstance(names, dict):
            return [names[i] for i in sorted(names.keys())]
        if isinstance(names, (list, tuple)):
            return list(names)
        return []

    try:
        ckpt = torch.load(pt_path, map_location="cpu", weights_only=False)

        # 1. Top-level 'names' key
        if isinstance(ckpt, dict) and "names" in ckpt:
            result = _names_to_list(ckpt["names"])
            if result:
                print(f"[classes] Found {len(result)} classes via ckpt['names']")
                return result

        # 2. model.names
        model_obj = None
        if isinstance(ckpt, dict):
            model_obj = ckpt.get("model") or ckpt.get("ema")
        else:
            model_obj = ckpt

        if model_obj is not None:
            for attr in ("names", "module.names"):
                obj = model_obj
                for part in attr.split("."):
                    obj = getattr(obj, part, None)
                    if obj is None:
                        break
                if obj is not None:
                    result = _names_to_list(obj)
                    if result:
                        print(f"[classes] Found {len(result)} classes via model.{attr}")
                        return result

        print(f"[classes] torch.load found no names, trying YOLO fallback...")

        # 3. YOLO fallback
        try:
            from ultralytics import YOLO
            yolo = YOLO(pt_path)
            if hasattr(yolo, "names") and yolo.names:
                result = _names_to_list(yolo.names)
                print(f"[classes] Found {len(result)} classes via YOLO")
                return result
        except Exception as yolo_err:
            print(f"[classes] YOLO fallback error: {yolo_err}")

    except Exception as e:
        print(f"[classes] extract_classes_from_pt error: {e}")
        traceback.print_exc()

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
            class_list = json.loads(classes)
        except Exception:
            raise HTTPException(status_code=422, detail="classes must be a JSON array.")

    asset_id = str(uuid.uuid4())
    model_dir = settings.models_dir / asset_id
    model_dir.mkdir(parents=True, exist_ok=True)
    pt_path = model_dir / "model.pt"
    print(f"[upload] Saving file to {pt_path}")

    with open(pt_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    print(f"[upload] File saved ({pt_path.stat().st_size} bytes)")

    if not class_list:
        class_list = extract_classes_from_pt(str(pt_path))
    print(f"[upload] Classes: {class_list}")

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

    rows = db.execute_query(ModelAssetQueries.GET_MODEL_BY_ID, {"id": asset_id})
    print(f"[upload] Upload complete for {asset_id}")
    return rows[0]


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
