"""
Database Table Definitions
Defines all PostgreSQL tables for the Flutter AI Studio
"""

import logging
from sqlalchemy import (
    Table,
    MetaData,
    Column,
    String,
    Text,
    DateTime,
    Boolean,
    JSON,
    Integer,
    ForeignKey,
    Index,
    text
)

logger = logging.getLogger(__name__)

metadata = MetaData()
DYNAMIC_TABLES = {}

def create_dynamic_table(name, columns, constraints=None, indexes=None):
    logger.debug(f"Creating table '{name}' with columns: {columns}")
    table_args = columns
    if constraints:
        table_args += constraints
    if indexes:
        table_args += indexes

    table = Table(name, metadata, *table_args)
    DYNAMIC_TABLES[name] = table
    logger.debug(f"Table '{name}' created and stored in DYNAMIC_TABLES.")
    return table

# =====================================================
# MODEL_ASSETS TABLE
# =====================================================
create_dynamic_table(
    "model_assets",
    [
        Column("id", String, primary_key=True),
        Column("vision_project_id", String, nullable=False),
        Column("vision_project_name", String, nullable=False),
        Column("model_type", String, nullable=False),
        Column("classes", JSON, default=list),
        Column("pt_path", String, nullable=True),
        Column("tflite_path", String, nullable=True),
        Column("labels_path", String, nullable=True),
        Column("status", String, default="pending"),
        Column("error_message", String, nullable=True),
        Column("conversion_log", String, nullable=True),
        Column("input_size", Integer, default=640),
        Column("vision_platform_url", String, default="http://localhost:8000"),
        Column("vision_platform_token", String, default=""),
        Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP"), nullable=False),
    ],
    indexes=[
        Index("idx_model_assets_status", "status"),
    ],
)

# =====================================================
# MASTER_MODEL_MAPPINGS TABLE
# =====================================================
create_dynamic_table(
    "master_model_mappings",
    [
        Column("id", String, primary_key=True),
        Column("platform_name", String, nullable=False),
        Column("model_code", String, nullable=False, unique=True),
        Column("description", String, nullable=True),
        Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP"), nullable=False),
    ],
    indexes=[
        Index("idx_master_model_code", "model_code"),
        Index("idx_master_platform_name", "platform_name"),
    ],
)

# =====================================================
# APP_PROJECTS TABLE
# =====================================================
create_dynamic_table(
    "app_projects",
    [
        Column("id", String, primary_key=True),
        Column("name", String, nullable=False),
        Column("package_name", String, nullable=False),
        Column("model_asset_id", String, nullable=True),
        Column("model_asset_ids", JSON, default=list),
        Column("inspection_tasks", JSON, default=list),
        Column("canvas_state", JSON, default=list),
        Column("app_settings", JSON, default=dict),
        Column("build_status", String, default="idle"),
        Column("build_log", String, nullable=True),
        Column("build_step", String, nullable=True),
        Column("apk_path", String, nullable=True),
        Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP"), nullable=False),
        Column("updated_at", DateTime, server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")),
    ]
)

# =====================================================
# INSPECTION_RESULTS TABLE
# =====================================================
create_dynamic_table(
    "inspection_results",
    [
        Column("id", String, primary_key=True),
        Column("app_id", String, ForeignKey("app_projects.id"), nullable=False),
        Column("vin", String, nullable=False),
        Column("model_code", String, nullable=False),
        Column("results", JSON, default=list), # List of {taskName, detectedPart, success}
        Column("overall_success", Boolean, default=True),
        Column("inspector_notes", Text, nullable=True),
        Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP"), nullable=False),
    ],
    indexes=[
        Index("idx_results_vin", "vin"),
        Index("idx_results_app_id", "app_id"),
    ]
)

logger.info("All table definitions created successfully")
