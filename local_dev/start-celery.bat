@echo off
call "%~dp0_config.bat"
call "%CONDA_ACTIVATE%" flutter-ai
cd /d "%PROJ%\backend"
echo Starting Celery worker (needs Redis running first).
celery -A app.tasks.celery_app worker --loglevel=info --pool=solo
