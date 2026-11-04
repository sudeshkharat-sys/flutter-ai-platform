@echo off
rem Opens each service in its own window. Redis and Postgres first.
call "%~dp0start-postgres.bat"
start "Redis" cmd /k "%~dp0start-redis.bat"
timeout /t 3 >nul
start "Backend" cmd /k "%~dp0start-backend.bat"
start "Celery" cmd /k "%~dp0start-celery.bat"
start "Frontend" cmd /k "%~dp0start-frontend.bat"
