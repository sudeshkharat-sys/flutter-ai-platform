from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime


# ── ModelAsset schemas ────────────────────────────────────────────────────────

class ModelAssetImport(BaseModel):
    vision_project_id: str
    vision_project_name: str
    model_type: str  # "seed" or "main"
    classes: List[str]
    vision_platform_url: Optional[str] = "http://localhost:8000"
    vision_platform_token: Optional[str] = ""
    input_size: Optional[int] = 640


class ModelAssetResponse(BaseModel):
    id: str
    vision_project_id: str
    vision_project_name: str
    model_type: str
    classes: List[str]
    pt_path: Optional[str]
    tflite_path: Optional[str]
    labels_path: Optional[str]
    status: str
    error_message: Optional[str]
    conversion_log: Optional[str] = ""
    input_size: int
    vision_platform_url: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ModelAssetStatus(BaseModel):
    id: str
    status: str
    error_message: Optional[str]
    conversion_log: Optional[str] = ""
    tflite_path: Optional[str]


# ── AppProject schemas ────────────────────────────────────────────────────────

class AppProjectCreate(BaseModel):
    name: str
    package_name: Optional[str] = None
    model_asset_id: Optional[str] = None
    model_asset_ids: Optional[List[str]] = []
    inspection_tasks: Optional[List[Any]] = []
    app_settings: Optional[dict] = None


class AppProjectUpdate(BaseModel):
    name: Optional[str] = None
    package_name: Optional[str] = None
    model_asset_id: Optional[str] = None
    model_asset_ids: Optional[List[str]] = None
    inspection_tasks: Optional[List[Any]] = None
    canvas_state: Optional[List[Any]] = None
    app_settings: Optional[dict] = None


class AppProjectResponse(BaseModel):
    id: str
    name: str
    package_name: str
    model_asset_id: Optional[str] = None
    model_asset_ids: List[str] = []
    inspection_tasks: List[Any] = []
    canvas_state: List[Any] = []
    app_settings: dict = {}
    created_at: datetime
    updated_at: datetime
    build_status: Optional[str] = "none"
    build_step: Optional[str] = None
    build_log: Optional[str] = None
    apk_path: Optional[str] = None
    build_error: Optional[str] = None

    model_config = {"from_attributes": True}


# ── MasterMapping schemas ─────────────────────────────────────────────────────

class MasterMappingCreate(BaseModel):
    platform_name: str
    model_code: str
    description: Optional[str] = None


class MasterMappingUpdate(BaseModel):
    platform_name: Optional[str] = None
    model_code: Optional[str] = None
    description: Optional[str] = None


class MasterMappingResponse(BaseModel):
    id: str
    platform_name: str
    model_code: str
    description: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}
