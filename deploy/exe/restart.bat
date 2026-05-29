@echo off
echo [1] Stopping processes...
taskkill /f /im flutterai.exe >nul 2>&1
taskkill /f /im postgres.exe >nul 2>&1
taskkill /f /im redis-server.exe >nul 2>&1

echo [2] Clearing data...
if exist "D:\FlutterAI-App\FlutterAI\data" rmdir /s /q "D:\FlutterAI-App\FlutterAI\data"

echo [3] Launching...
start "" "D:\FlutterAI-App\FlutterAI\flutterai.exe"
