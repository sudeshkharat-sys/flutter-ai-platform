import uuid
from fastapi import APIRouter, Depends, HTTPException
from app.connectors.state_db import StateDBConnector
from app.queries import EngineDataQueries
from app.schemas.base import EngineMappingCreate, EngineMappingUpdate, EngineMappingResponse

router = APIRouter(prefix="/engine-data", tags=["engine"])

def get_db_connector():
    connector = StateDBConnector()
    try:
        yield connector
    finally:
        pass

@router.get("", response_model=list[EngineMappingResponse])
def list_engine_mappings(db: StateDBConnector = Depends(get_db_connector)):
    rows = db.execute_query(EngineDataQueries.GET_ALL_MAPPINGS)
    return rows

@router.post("", response_model=EngineMappingResponse)
def create_engine_mapping(data: EngineMappingCreate, db: StateDBConnector = Depends(get_db_connector)):
    mapping_id = str(uuid.uuid4())
    params = {
        "id": mapping_id,
        "sheet_name": data.sheet_name.strip().upper(),
        "part_no": data.part_no.strip(),
        "model_name": data.model_name.strip() if data.model_name else None,
        "description": data.description.strip() if data.description else None,
    }
    try:
        db.execute_insert(EngineDataQueries.INSERT_MAPPING, params)
    except Exception:
        raise HTTPException(status_code=400, detail="Part number already exists or database error")

    rows = db.execute_query(EngineDataQueries.GET_MAPPING_BY_ID, {"id": mapping_id})
    if not rows:
        raise HTTPException(status_code=500, detail="Failed to create mapping")
    return rows[0]

@router.put("/{mapping_id}", response_model=EngineMappingResponse)
def update_engine_mapping(mapping_id: str, data: EngineMappingUpdate, db: StateDBConnector = Depends(get_db_connector)):
    rows = db.execute_query(EngineDataQueries.GET_MAPPING_BY_ID, {"id": mapping_id})
    if not rows:
        raise HTTPException(status_code=404, detail="Mapping not found")
    existing = rows[0]

    params = {
        "id": mapping_id,
        "sheet_name": data.sheet_name.strip().upper() if data.sheet_name is not None else existing["sheet_name"],
        "part_no": data.part_no.strip() if data.part_no is not None else existing["part_no"],
        "model_name": data.model_name.strip() if data.model_name is not None else existing["model_name"],
        "description": data.description.strip() if data.description is not None else existing["description"],
    }
    db.execute_update(EngineDataQueries.UPDATE_MAPPING, params)

    rows = db.execute_query(EngineDataQueries.GET_MAPPING_BY_ID, {"id": mapping_id})
    return rows[0]

@router.delete("/{mapping_id}")
def delete_engine_mapping(mapping_id: str, db: StateDBConnector = Depends(get_db_connector)):
    deleted = db.execute_update(EngineDataQueries.DELETE_MAPPING, {"id": mapping_id})
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return {"deleted": True}
