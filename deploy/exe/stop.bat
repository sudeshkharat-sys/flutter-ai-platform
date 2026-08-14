@echo off
echo ============================================================
echo  Flutter AI Studio - Stop All Services
echo ============================================================

echo [1] Stopping Flutter AI Studio...
taskkill /f /im flutterai.exe >nul 2>&1

echo [2] Stopping PostgreSQL...
taskkill /f /im postgres.exe >nul 2>&1

echo [3] Stopping Redis...
taskkill /f /im redis-server.exe >nul 2>&1

echo [4] Stopping any leftover Java/Gradle processes...
taskkill /f /im java.exe >nul 2>&1

echo.
echo All services stopped.
echo ============================================================
