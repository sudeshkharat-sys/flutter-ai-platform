import json
import io
import os
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse
from app.connectors.state_db import StateDBConnector
from app.queries import ProjectQueries, ModelAssetQueries
from app.codegen.generator import generate_flutter_project
from app.tasks.build_apk import build_apk_task

router = APIRouter(prefix="/apps", tags=["export"])

def get_db_connector():
    connector = StateDBConnector()
    try:
        yield connector
    finally:
        pass

@router.post("/{app_id}/build")
def trigger_build(app_id: str, db: StateDBConnector = Depends(get_db_connector)):
    """Trigger an APK build for the app."""
    rows = db.execute_query(ProjectQueries.GET_PROJECT_BY_ID, {"id": app_id})
    if not rows:
        raise HTTPException(status_code=404, detail="App project not found")
    
    app = rows[0]
    
    model_asset_ids = app.get("model_asset_ids", [])
    if isinstance(model_asset_ids, str):
        model_asset_ids = json.loads(model_asset_ids)

    if not model_asset_ids or len(model_asset_ids) == 0:
        raise HTTPException(status_code=400, detail="No model assigned to this app. Please add a model in the Studio UI first.")

    params = {
        "id": app_id,
        "name": app["name"],
        "package_name": app["package_name"],
        "model_asset_id": app.get("model_asset_id"),
        "model_asset_ids": json.dumps(model_asset_ids),
        "inspection_tasks": json.dumps(app.get("inspection_tasks", [])),
        "canvas_state": json.dumps(app.get("canvas_state", [])),
        "app_settings": json.dumps(app.get("app_settings", {})),
        "build_status": "building",
        "build_log": "Build triggered via UI.\n",
        "build_step": "Starting Celery task...",
        "apk_path": ""
    }
    
    db.execute_update(ProjectQueries.UPDATE_PROJECT, params)

    # In a real environment with Celery worker running:
    build_apk_task.delay(app_id)
    
    return {"status": "building", "message": "APK build started in background"}

@router.get("/{app_id}/apk")
def download_apk(app_id: str, db: StateDBConnector = Depends(get_db_connector)):
    """Download the built APK."""
    rows = db.execute_query(ProjectQueries.GET_PROJECT_BY_ID, {"id": app_id})
    if not rows:
        raise HTTPException(status_code=404, detail="App project not found")
    
    app = rows[0]
    if app["build_status"] != "ready" or not app.get("apk_path"):
        raise HTTPException(status_code=400, detail=f"APK not ready. Status: {app['build_status']}")
    
    if not os.path.exists(app["apk_path"]):
        raise HTTPException(status_code=404, detail="APK file not found on disk")

    return FileResponse(
        app["apk_path"],
        media_type="application/vnd.android.package-archive",
        filename=f"{app['name'].lower().replace(' ', '_')}.apk"
    )

@router.post("/{app_id}/export")
def export_app(app_id: str, db: StateDBConnector = Depends(get_db_connector)):
    rows = db.execute_query(ProjectQueries.GET_PROJECT_BY_ID, {"id": app_id})
    if not rows:
        raise HTTPException(status_code=404, detail="App project not found")
    
    app = rows[0]
    all_model_assets = []
    
    model_asset_ids = app.get("model_asset_ids", [])
    if isinstance(model_asset_ids, str):
        model_asset_ids = json.loads(model_asset_ids)

    if model_asset_ids:
        # Construct dynamic IN clause
        placeholders = ", ".join([f":id{i}" for i in range(len(model_asset_ids))])
        query = f"SELECT * FROM model_assets WHERE id IN ({placeholders})"
        params = {f"id{i}": mid for i, mid in enumerate(model_asset_ids)}
        models = db.execute_query(query, params)
        all_model_assets = models

    # Note: We need to adapt the generator to accept dicts instead of ORM objects
    # This might require some adaptation in app.codegen.generator.generate_flutter_project
    # For now, we will pass dicts.
    zip_bytes = generate_flutter_project(app, all_model_assets=all_model_assets)

    safe_name = app["name"].lower().replace(" ", "_")
    filename = f"{safe_name}_flutter.zip"

    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@router.get("/{app_id}/preview")
def preview_export(app_id: str, db: StateDBConnector = Depends(get_db_connector)):
    """Return the template context that would be used for code generation."""
    rows = db.execute_query(ProjectQueries.GET_PROJECT_BY_ID, {"id": app_id})
    if not rows:
        raise HTTPException(status_code=404, detail="App project not found")

    app = rows[0]
    model_asset = None
    
    model_asset_id = app.get("model_asset_id")
    if model_asset_id:
        m_rows = db.execute_query(ModelAssetQueries.GET_MODEL_BY_ID, {"id": model_asset_id})
        if m_rows:
            model_asset = m_rows[0]

    app_settings = app.get("app_settings", {})
    if isinstance(app_settings, str):
        app_settings = json.loads(app_settings)
        
    canvas_state = app.get("canvas_state", [])
    if isinstance(canvas_state, str):
        canvas_state = json.loads(canvas_state)

    classes = []
    input_size = 640
    model_name = "No model"
    
    if model_asset:
        model_name = model_asset.get("vision_project_name", "Unknown")
        input_size = model_asset.get("input_size", 640)
        classes = model_asset.get("classes", [])
        if isinstance(classes, str):
            classes = json.loads(classes)

    return {
        "app_name": app["name"],
        "package_name": app["package_name"],
        "classes": classes,
        "canvas_widgets": canvas_state,
        "confidence_threshold": app_settings.get("confidence_threshold", 0.5),
        "show_labels": app_settings.get("show_labels", True),
        "show_confidence": app_settings.get("show_confidence", True),
        "input_size": input_size,
        "model_name": model_name,
    }
