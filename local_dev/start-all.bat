@echo off
rem One command: Postgres, Redis, backend, Celery, frontend (each in its own window).
call "%~dp0_config.bat"
call "%~dp0start-postgres.bat"
start "%PROJECT_NAME% Redis" cmd /k "%~dp0start-redis.bat"
timeout /t 3 >nul
start "%PROJECT_NAME% Backend" cmd /k "%~dp0start-backend.bat"
start "%PROJECT_NAME% Celery" cmd /k "%~dp0start-celery.bat"
start "%PROJECT_NAME% Frontend" cmd /k "%~dp0start-frontend.bat"
