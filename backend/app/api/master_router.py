import uuid
from fastapi import APIRouter, Depends, HTTPException
from app.connectors.state_db import StateDBConnector
from app.queries import MasterDataQueries
from app.schemas.base import MasterMappingCreate, MasterMappingResponse

router = APIRouter(prefix="/master-data", tags=["master"])

def get_db_connector():
    connector = StateDBConnector()
    try:
        yield connector
    finally:
        pass

@router.get("", response_model=list[MasterMappingResponse])
def list_master_mappings(db: StateDBConnector = Depends(get_db_connector)):
    rows = db.execute_query(MasterDataQueries.GET_ALL_MAPPINGS)
    return rows

@router.post("", response_model=MasterMappingResponse)
def create_master_mapping(data: MasterMappingCreate, db: StateDBConnector = Depends(get_db_connector)):
    mapping_id = str(uuid.uuid4())
    params = {
        "id": mapping_id,
        "platform_name": data.platform_name,
        "model_code": data.model_code,
        "description": data.description,
    }
    
    try:
        db.execute_insert(MasterDataQueries.INSERT_MAPPING, params)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Model code already exists or database error")
    
    rows = db.execute_query(MasterDataQueries.GET_MAPPING_BY_ID, {"id": mapping_id})
    if not rows:
        raise HTTPException(status_code=500, detail="Failed to create mapping")
    return rows[0]

@router.delete("/{mapping_id}")
def delete_master_mapping(mapping_id: str, db: StateDBConnector = Depends(get_db_connector)):
    deleted = db.execute_update(MasterDataQueries.DELETE_MAPPING, {"id": mapping_id})
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return {"deleted": True}
