@echo off
rem One command: Postgres, Redis, backend, Celery, frontend.
rem With Windows Terminal (wt.exe): ONE window with a tab per service.
rem Without it: a separate window per service.
call "%~dp0_config.bat"
call "%~dp0start-postgres.bat"
where wt >nul 2>&1
if %errorlevel%==0 (
  wt --window new new-tab --suppressApplicationTitle --title "%PROJECT_NAME% Redis" cmd /k "%~dp0start-redis.bat" ; new-tab --suppressApplicationTitle --title "%PROJECT_NAME% Backend" cmd /k "%~dp0start-backend.bat" ; new-tab --suppressApplicationTitle --title "%PROJECT_NAME% Celery" cmd /k "%~dp0start-celery.bat" ; new-tab --suppressApplicationTitle --title "%PROJECT_NAME% Frontend" cmd /k "%~dp0start-frontend.bat"
) else (
  start "%PROJECT_NAME% Redis" cmd /k "%~dp0start-redis.bat"
  timeout /t 3 >nul
  start "%PROJECT_NAME% Backend" cmd /k "%~dp0start-backend.bat"
  start "%PROJECT_NAME% Celery" cmd /k "%~dp0start-celery.bat"
  start "%PROJECT_NAME% Frontend" cmd /k "%~dp0start-frontend.bat"
)
