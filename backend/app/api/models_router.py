import json
import os
import shutil
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
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
    """Helper to load a .pt model and extract class names."""
    from ultralytics import YOLO
    try:
        model = YOLO(pt_path)
        if hasattr(model, 'names') and model.names:
            return [model.names[i] for i in sorted(model.names.keys())]
    except Exception as e:
        print(f"Error extracting classes: {e}")
    return []

@router.post("/extract-classes")
def extract_classes_from_file(file: UploadFile = File(...)):
    """Temporary upload to just extract classes from a .pt file."""
    if not file.filename.endswith(".pt"):
        raise HTTPException(status_code=422, detail="Only .pt model files are supported.")
    
    temp_id = str(uuid.uuid4())
    temp_path = settings.models_dir / f"temp_{temp_id}.pt"
    
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
    if classes:
        # We need an update query for classes if we want to save it, 
        # but for now we can just return it. To properly save, we'd need an update query.
        pass
    
    return {"classes": classes}

@router.post("/upload", response_model=ModelAssetResponse)
def upload_model(
    file: UploadFile = File(...),
    model_name: str = Form(...),
    classes: str = Form(None),
    input_size: int = Form(640),
    db: StateDBConnector = Depends(get_db_connector),
):
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

    with open(pt_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    if not class_list:
        class_list = extract_classes_from_pt(str(pt_path))

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

    db.execute_insert(ModelAssetQueries.INSERT_MODEL, params)

    convert_model_to_tflite.delay(asset_id)

    rows = db.execute_query(ModelAssetQueries.GET_MODEL_BY_ID, {"id": asset_id})
    return rows[0]

@router.post("/upload-ocr", response_model=ModelAssetResponse)
def upload_ocr_model(
    tflite_file: UploadFile = File(...),
    charset_file: UploadFile = File(...),
    model_name: str = Form(...),
    model_kind: str = Form(...),
    meta_file: UploadFile = File(None),
    db: StateDBConnector = Depends(get_db_connector),
):
    """
    Register an already-built OCR model bundle (a `.tflite` exported from
    ai-vision-platform's OCR trainer, plus its charset/labels sidecar and
    optional meta.json). Unlike /upload, this is NOT a YOLO .pt -- it's
    already tflite, so it's registered directly with status="ready" and
    never queued for conversion.
    """
    if model_kind not in ("detector", "ocr_cnn", "ocr_crnn"):
        raise HTTPException(status_code=422, detail="model_kind must be one of: detector, ocr_cnn, ocr_crnn")
    if not tflite_file.filename.endswith(".tflite"):
        raise HTTPException(status_code=422, detail="tflite_file must be a .tflite file.")

    asset_id = str(uuid.uuid4())
    model_dir = settings.models_dir / asset_id
    model_dir.mkdir(parents=True, exist_ok=True)

    tflite_path = model_dir / "model.tflite"
    with open(tflite_path, "wb") as f:
        shutil.copyfileobj(tflite_file.file, f)

    charset_path = model_dir / "charset.txt"
    with open(charset_path, "wb") as f:
        shutil.copyfileobj(charset_file.file, f)
    class_list = [
        line.strip() for line in charset_path.read_text().splitlines() if line.strip()
    ]

    meta_path = None
    if meta_file is not None and meta_file.filename:
        meta_path = model_dir / "meta.json"
        with open(meta_path, "wb") as f:
            shutil.copyfileobj(meta_file.file, f)

    params = {
        "id": asset_id,
        "vision_project_id": asset_id,
        "vision_project_name": model_name,
        "model_type": "uploaded",
        "model_kind": model_kind,
        "classes": json.dumps(class_list),
        "pt_path": None,
        "tflite_path": str(tflite_path),
        "labels_path": str(charset_path),
        "charset_path": str(charset_path),
        "meta_path": str(meta_path) if meta_path else None,
        "status": "ready",
        "error_message": None,
        "conversion_log": "Registered pre-built OCR tflite bundle -- no conversion needed.\n",
        "input_size": 0,
        "vision_platform_url": "",
        "vision_platform_token": "",
    }

    db.execute_insert(ModelAssetQueries.INSERT_OCR_MODEL, params)

    rows = db.execute_query(ModelAssetQueries.GET_MODEL_BY_ID, {"id": asset_id})
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

@router.get("/{model_asset_id}/download-pt")
def download_pt(model_asset_id: str, db: StateDBConnector = Depends(get_db_connector)):
    """Download the original uploaded .pt model file, e.g. to resume training."""
    rows = db.execute_query(ModelAssetQueries.GET_MODEL_BY_ID, {"id": model_asset_id})
    if not rows:
        raise HTTPException(status_code=404, detail="Model asset not found")

    asset = rows[0]
    if not asset["pt_path"]:
        raise HTTPException(status_code=404, detail="Model asset missing pt_path")

    if not os.path.exists(asset["pt_path"]):
        raise HTTPException(status_code=404, detail="PT file not found on disk")

    filename = f"{asset['vision_project_name'].lower().replace(' ', '_')}.pt"
    return FileResponse(
        asset["pt_path"],
        media_type="application/octet-stream",
        filename=filename,
    )

@router.delete("/{model_asset_id}")
def delete_model(model_asset_id: str, db: StateDBConnector = Depends(get_db_connector)):
    deleted = db.execute_update(ModelAssetQueries.DELETE_MODEL, {"id": model_asset_id})
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Model asset not found")
    return {"deleted": True}
