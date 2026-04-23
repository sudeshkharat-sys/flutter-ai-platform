import uuid
import json
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from app.connectors.state_db import StateDBConnector
from app.queries import ProjectQueries
from app.schemas.base import AppProjectCreate, AppProjectUpdate, AppProjectResponse

router = APIRouter(prefix="/apps", tags=["apps"])

def get_db_connector():
    connector = StateDBConnector()
    try:
        yield connector
    finally:
        pass # Let the connector handle its own connection pooling

def parse_json_field(val, default):
    if val is None: return default
    if isinstance(val, (list, dict)): return val
    try:
        return json.loads(val)
    except:
        return default

@router.get("", response_model=list[AppProjectResponse])
def list_apps(db: StateDBConnector = Depends(get_db_connector)):
    rows = db.execute_query(ProjectQueries.GET_ALL_PROJECTS)
    # Ensure JSON fields are parsed for the response model
    results = []
    for row in rows:
        app = dict(row)
        app["model_asset_ids"] = parse_json_field(app.get("model_asset_ids"), [])
        app["inspection_tasks"] = parse_json_field(app.get("inspection_tasks"), [])
        app["canvas_state"] = parse_json_field(app.get("canvas_state"), [])
        app["app_settings"] = parse_json_field(app.get("app_settings"), {})
        results.append(app)
    return results

@router.post("", response_model=AppProjectResponse)
def create_app(data: AppProjectCreate, db: StateDBConnector = Depends(get_db_connector)):
    project_id = str(uuid.uuid4())
    package_name = data.package_name
    if not package_name:
        slug = data.name.lower().replace(" ", "_").replace("-", "_")
        package_name = f"com.studio.{slug}"

    app_settings = data.app_settings or {
        "confidence_threshold": 0.5,
        "show_labels": True,
        "show_confidence": True,
        "color_scheme": "dark",
    }

    params = {
        "id": project_id,
        "name": data.name,
        "package_name": package_name,
        "model_asset_id": data.model_asset_id,
        "model_asset_ids": json.dumps(data.model_asset_ids or []),
        "inspection_tasks": json.dumps(data.inspection_tasks or []),
        "canvas_state": json.dumps([]),
        "app_settings": json.dumps(app_settings),
        "build_status": "idle",
        "build_log": "",
        "build_step": "",
        "apk_path": ""
    }
    
    db.execute_insert(ProjectQueries.INSERT_PROJECT, params)
    
    # Fetch and return the created project
    rows = db.execute_query(ProjectQueries.GET_PROJECT_BY_ID, {"id": project_id})
    if not rows:
        raise HTTPException(status_code=500, detail="Failed to create project")
    
    app = dict(rows[0])
    app["model_asset_ids"] = parse_json_field(app.get("model_asset_ids"), [])
    app["inspection_tasks"] = parse_json_field(app.get("inspection_tasks"), [])
    app["canvas_state"] = parse_json_field(app.get("canvas_state"), [])
    app["app_settings"] = parse_json_field(app.get("app_settings"), {})
    return app

@router.get("/{app_id}", response_model=AppProjectResponse)
def get_app(app_id: str, db: StateDBConnector = Depends(get_db_connector)):
    rows = db.execute_query(ProjectQueries.GET_PROJECT_BY_ID, {"id": app_id})
    if not rows:
        raise HTTPException(status_code=404, detail="App project not found")
    
    app = dict(rows[0])
    app["model_asset_ids"] = parse_json_field(app.get("model_asset_ids"), [])
    app["inspection_tasks"] = parse_json_field(app.get("inspection_tasks"), [])
    app["canvas_state"] = parse_json_field(app.get("canvas_state"), [])
    app["app_settings"] = parse_json_field(app.get("app_settings"), {})
    return app

@router.patch("/{app_id}", response_model=AppProjectResponse)
def update_app(app_id: str, data: AppProjectUpdate, db: StateDBConnector = Depends(get_db_connector)):
    rows = db.execute_query(ProjectQueries.GET_PROJECT_BY_ID, {"id": app_id})
    if not rows:
        raise HTTPException(status_code=404, detail="App project not found")
    
    app = dict(rows[0])
    
    # Parse existing JSON fields to avoid double-encoding
    current_asset_ids = parse_json_field(app.get("model_asset_ids"), [])
    current_tasks = parse_json_field(app.get("inspection_tasks"), [])
    current_canvas = parse_json_field(app.get("canvas_state"), [])
    current_settings = parse_json_field(app.get("app_settings"), {})

    # Update fields
    name = data.name if data.name is not None else app["name"]
    package_name = data.package_name if data.package_name is not None else app["package_name"]
    model_asset_id = data.model_asset_id if data.model_asset_id is not None else app.get("model_asset_id")
    
    model_asset_ids = data.model_asset_ids if data.model_asset_ids is not None else current_asset_ids
    inspection_tasks = data.inspection_tasks if data.inspection_tasks is not None else current_tasks
    canvas_state = data.canvas_state if data.canvas_state is not None else current_canvas
    
    app_settings = current_settings
    if data.app_settings is not None:
        app_settings = {**app_settings, **data.app_settings}

    params = {
        "id": app_id,
        "name": name,
        "package_name": package_name,
        "model_asset_id": model_asset_id,
        "model_asset_ids": json.dumps(model_asset_ids),
        "inspection_tasks": json.dumps(inspection_tasks),
        "canvas_state": json.dumps(canvas_state),
        "app_settings": json.dumps(app_settings),
        "build_status": app.get("build_status", "idle"),
        "build_log": app.get("build_log", ""),
        "build_step": app.get("build_step", ""),
        "apk_path": app.get("apk_path", "")
    }

    db.execute_update(ProjectQueries.UPDATE_PROJECT, params)
    
    updated_rows = db.execute_query(ProjectQueries.GET_PROJECT_BY_ID, {"id": app_id})
    res = dict(updated_rows[0])
    res["model_asset_ids"] = parse_json_field(res.get("model_asset_ids"), [])
    res["inspection_tasks"] = parse_json_field(res.get("inspection_tasks"), [])
    res["canvas_state"] = parse_json_field(res.get("canvas_state"), [])
    res["app_settings"] = parse_json_field(res.get("app_settings"), {})
    return res

@router.delete("/{app_id}")
def delete_app(app_id: str, db: StateDBConnector = Depends(get_db_connector)):
    deleted = db.execute_update(ProjectQueries.DELETE_PROJECT, {"id": app_id})
    if deleted == 0:
        raise HTTPException(status_code=404, detail="App project not found")
    return {"deleted": True}

@router.post("/{app_id}/submit")
def submit_inspection(app_id: str, data: dict, db: StateDBConnector = Depends(get_db_connector)):
    """Receive and persist inspection results from the mobile app."""
    result_id = str(uuid.uuid4())
    params = {
        "id": result_id,
        "app_id": app_id,
        "vin": data.get("vin", "UNKNOWN"),
        "model_code": data.get("modelCode", "UNKNOWN"),
        "results": json.dumps(data.get("results", [])),
        "overall_success": data.get("overallSuccess", True),
        "inspector_notes": data.get("notes", ""),
    }
    
    from app.queries import ResultQueries
    db.execute_insert(ResultQueries.INSERT_RESULT, params)
    return {"id": result_id, "status": "success"}

@router.get("/{app_id}/results")
def list_results(app_id: str, db: StateDBConnector = Depends(get_db_connector)):
    from app.queries import ResultQueries
    rows = db.execute_query(ResultQueries.GET_RESULTS_BY_APP, {"app_id": app_id})
    # Parse JSON results
    for row in rows:
        row["results"] = parse_json_field(row.get("results"), [])
    return rows
