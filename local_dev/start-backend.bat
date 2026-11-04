@echo off
call "%~dp0_config.bat"
call "%CONDA_ACTIVATE%" flutter-ai
cd /d "%PROJ%\backend"
echo Starting backend on http://127.0.0.1:8001
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
