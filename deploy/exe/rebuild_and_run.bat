@echo off
echo ============================================================
echo  Flutter AI Studio - Clean Rebuild and Run
echo ============================================================

:: Kill any running instances
echo [1] Killing any running processes...
taskkill /f /im flutterai.exe >nul 2>&1
taskkill /f /im postgres.exe >nul 2>&1
taskkill /f /im redis-server.exe >nul 2>&1
echo     Done.

:: Delete runtime data (database, logs) from previous runs
echo [2] Cleaning previous data...
if exist "D:\FlutterAI-App\FlutterAI\data" (
    rmdir /s /q "D:\FlutterAI-App\FlutterAI\data"
    echo     Data cleared.
) else (
    echo     Nothing to clear.
)

:: Pull latest code
echo [3] Pulling latest code...
pushd "%~dp0..\.."
git pull origin claude/funny-gauss-F8POw
popd

:: Rebuild EXE
echo [4] Rebuilding EXE...
call conda activate flutterai
cd /d "%~dp0"
call .\build.bat
if errorlevel 1 ( echo [ERROR] Build failed. & exit /b 1 )

:: Run
echo [5] Launching...
start "" "D:\FlutterAI-App\FlutterAI\flutterai.exe"
