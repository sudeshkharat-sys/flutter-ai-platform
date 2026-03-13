"""
Database Queries
SQL queries for the Flutter AI Studio platform.
"""

class DatabaseQueries:
    CHECK_DATABASE_EXISTS = "SELECT 1 FROM pg_database WHERE datname = :db_name"
    
    @staticmethod
    def get_create_database_query(db_name: str) -> str:
        # Note: psycopg2 doesn't allow parameterization of database names, so we format securely
        return f"CREATE DATABASE \"{db_name}\""

class CommonQueries:
    TEST_CONNECTION = "SELECT 1"

class ProjectQueries:
    GET_ALL_PROJECTS = "SELECT * FROM app_projects ORDER BY created_at DESC"
    GET_PROJECT_BY_ID = "SELECT * FROM app_projects WHERE id = :id"
    INSERT_PROJECT = """
        INSERT INTO app_projects (id, name, package_name, model_asset_id, model_asset_ids, inspection_tasks, canvas_state, app_settings, build_status, build_log, build_step, apk_path, created_at, updated_at)
        VALUES (:id, :name, :package_name, :model_asset_id, :model_asset_ids, :inspection_tasks, :canvas_state, :app_settings, :build_status, :build_log, :build_step, :apk_path, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        RETURNING id
    """
    UPDATE_PROJECT = """
        UPDATE app_projects
        SET name = :name, package_name = :package_name, model_asset_id = :model_asset_id, model_asset_ids = :model_asset_ids, inspection_tasks = :inspection_tasks, canvas_state = :canvas_state, app_settings = :app_settings, build_status = :build_status, build_log = :build_log, build_step = :build_step, apk_path = :apk_path, updated_at = CURRENT_TIMESTAMP
        WHERE id = :id
    """
    DELETE_PROJECT = "DELETE FROM app_projects WHERE id = :id"

class MasterDataQueries:
    GET_ALL_MAPPINGS = "SELECT * FROM master_model_mappings ORDER BY created_at DESC"
    GET_MAPPING_BY_ID = "SELECT * FROM master_model_mappings WHERE id = :id"
    INSERT_MAPPING = """
        INSERT INTO master_model_mappings (id, platform_name, model_code, description, created_at)
        VALUES (:id, :platform_name, :model_code, :description, CURRENT_TIMESTAMP)
        RETURNING id
    """
    DELETE_MAPPING = "DELETE FROM master_model_mappings WHERE id = :id"

class ResultQueries:
    INSERT_RESULT = """
        INSERT INTO inspection_results (id, app_id, vin, model_code, results, overall_success, inspector_notes, created_at)
        VALUES (:id, :app_id, :vin, :model_code, :results, :overall_success, :inspector_notes, CURRENT_TIMESTAMP)
        RETURNING id
    """
    GET_RESULTS_BY_APP = "SELECT * FROM inspection_results WHERE app_id = :app_id ORDER BY created_at DESC"

class ModelAssetQueries:
    GET_ALL_MODELS = "SELECT * FROM model_assets ORDER BY created_at DESC"
    GET_MODEL_BY_ID = "SELECT * FROM model_assets WHERE id = :id"
    INSERT_MODEL = """
        INSERT INTO model_assets (id, vision_project_id, vision_project_name, model_type, classes, pt_path, tflite_path, labels_path, status, error_message, conversion_log, input_size, vision_platform_url, vision_platform_token, created_at)
        VALUES (:id, :vision_project_id, :vision_project_name, :model_type, :classes, :pt_path, :tflite_path, :labels_path, :status, :error_message, :conversion_log, :input_size, :vision_platform_url, :vision_platform_token, CURRENT_TIMESTAMP)
        RETURNING id
    """
    UPDATE_MODEL_STATUS = """
        UPDATE model_assets
        SET status = :status, error_message = :error_message, conversion_log = :conversion_log
        WHERE id = :id
    """
    DELETE_MODEL = "DELETE FROM model_assets WHERE id = :id"

class QueryValidator:
    @staticmethod
    def validate_identifier(identifier: str, context: str = "identifier"):
        if not identifier or not identifier.replace("_", "").isalnum():
            raise ValueError(f"Invalid {context}: only alphanumeric characters and underscores are allowed")
