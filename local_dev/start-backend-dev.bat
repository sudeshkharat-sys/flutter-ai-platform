@echo off
rem Dev backend with auto-reload, watching ONLY the app code (not uploads/models/exports).
call "%~dp0_config.bat"
call "%CONDA_ACTIVATE%" %CONDA_ENV%
cd /d "%PROJ%\backend"
echo [%PROJECT_NAME%] DEV backend (reload on app\*.py) on http://127.0.0.1:%BACKEND_PORT%
uvicorn %BACKEND_APP% --host 127.0.0.1 --port %BACKEND_PORT% --reload --reload-dir app --reload-include "*.py"
