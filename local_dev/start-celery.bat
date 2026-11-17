@echo off
rem Celery worker with auto-restart. Jobs interrupted by a crash are marked
rem "Interrupted - retry" on the next worker start (see celery_app.py).
call "%~dp0_config.bat"
call "%CONDA_ACTIVATE%" %CONDA_ENV%
cd /d "%PROJ%\backend"
:loop
echo [%PROJECT_NAME%] Celery worker (Redis must be running first).  (%date% %time%)
celery -A %CELERY_APP% worker --loglevel=info --pool=solo
echo.
echo [%PROJECT_NAME%] celery exited (code %errorlevel%). Restarting in 5s - close this tab to stop.
timeout /t 5 >nul
goto loop
