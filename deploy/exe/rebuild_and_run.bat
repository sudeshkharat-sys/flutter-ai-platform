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

:: Delete runtime data using robocopy to handle long paths from APK/Gradle builds
echo [2] Cleaning previous data...
if exist "D:\FlutterAI-App\FlutterAI\data" (
    mkdir "%TEMP%\__pyi_empty__" >nul 2>&1
    robocopy "%TEMP%\__pyi_empty__" "D:\FlutterAI-App\FlutterAI\data" /MIR /NFL /NDL /NJH /NJS /NC /NS /NP >nul 2>&1
    rmdir /s /q "D:\FlutterAI-App\FlutterAI\data" >nul 2>&1
    rmdir /s /q "%TEMP%\__pyi_empty__" >nul 2>&1
    echo     Data cleared.
) else (
    echo     Nothing to clear.
)

:: Pull latest code (whichever branch is currently checked out)
echo [3] Pulling latest code...
pushd "%~dp0..\.."
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD') do set "CURRENT_BRANCH=%%b"
echo     Branch: %CURRENT_BRANCH%
git pull origin %CURRENT_BRANCH%
if errorlevel 1 ( echo [ERROR] git pull failed - resolve conflicts manually, then re-run. & popd & exit /b 1 )
popd

:: Rebuild EXE
echo [4] Rebuilding EXE...
call conda activate flutter-ai
cd /d "%~dp0"
call .\build.bat
if errorlevel 1 ( echo [ERROR] Build failed. & exit /b 1 )

:: Run
echo [5] Launching...
start "" "D:\FlutterAI-App\FlutterAI\flutterai.exe"
