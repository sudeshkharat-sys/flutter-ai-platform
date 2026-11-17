@echo off
rem Stable backend: NO --reload (file changes must not kill a running request) and
rem auto-restart if it crashes. Use start-backend-dev.bat when editing backend code.
call "%~dp0_config.bat"
call "%CONDA_ACTIVATE%" %CONDA_ENV%
cd /d "%PROJ%\backend"
:loop
echo [%PROJECT_NAME%] backend on http://127.0.0.1:%BACKEND_PORT%  (%date% %time%)
uvicorn %BACKEND_APP% --host 127.0.0.1 --port %BACKEND_PORT%
echo.
echo [%PROJECT_NAME%] backend exited (code %errorlevel%). Restarting in 5s - close this tab to stop.
timeout /t 5 >nul
goto loop
