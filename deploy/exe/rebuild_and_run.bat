@echo off
setlocal EnableDelayedExpansion
echo ============================================================
echo  Flutter AI Studio - Rebuild and Run
echo ============================================================

:: --------------------------------------------------------------------------
:: Mode select: pass it as an argument (rebuild_and_run.bat full / keep) to
:: skip the prompt, e.g. for unattended/scripted use.
::   full - wipe EVERYTHING (database, models, exports) and start fresh
::   keep - update the app only; database/models/exports are untouched
:: --------------------------------------------------------------------------
set "MODE=%~1"
if "%MODE%"=="" (
    echo.
    echo  [1] Update ^& Keep Data  - rebuild the app, keep your database/models  ^(recommended^)
    echo  [2] Full Rebuild         - wipe EVERYTHING and start fresh
    echo.
    choice /c 12 /n /m "Choose an option (1 or 2): "
    if errorlevel 2 (set "MODE=full") else (set "MODE=keep")
)
if /i "%MODE%"=="full" (set "MODE=full") else (set "MODE=keep")
echo     Mode: %MODE%

:: Kill any running instances of THIS app only. Plain "taskkill /im
:: postgres.exe" / "redis-server.exe" matches by process name only, with no
:: regard for which install started it -- on a machine that also runs a
:: system/other-project Postgres (e.g. another backend on port 5432), that
:: silently kills it too and looks like "my other project's DB just died"
:: with no obvious link back to this script. Only kill postgres.exe /
:: redis-server.exe whose executable actually lives under our own
:: FlutterAI-App output folder.
echo [1] Killing any running processes...
taskkill /f /im flutterai.exe >nul 2>&1
powershell -NoProfile -Command ^
    "Get-CimInstance Win32_Process -Filter \"Name='postgres.exe' OR Name='redis-server.exe'\" | " ^
    "Where-Object { $_.ExecutablePath -like '*FlutterAI-App*' } | " ^
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo     Done.

if /i "%MODE%"=="full" (
    echo [2] Full Rebuild - wiping all data ^(database, models, exports^)...
    if exist "D:\FlutterAI-App\FlutterAI\data" (
        mkdir "%TEMP%\__pyi_empty__" >nul 2>&1
        robocopy "%TEMP%\__pyi_empty__" "D:\FlutterAI-App\FlutterAI\data" /MIR /NFL /NDL /NJH /NJS /NC /NS /NP >nul 2>&1
        rmdir /s /q "D:\FlutterAI-App\FlutterAI\data" >nul 2>&1
        rmdir /s /q "%TEMP%\__pyi_empty__" >nul 2>&1
        echo     All data cleared.
    ) else (
        echo     Nothing to clear.
    )
    set "FLUTTERAI_FULL_REBUILD=1"
) else (
    :: Clear ONLY the Flutter/Gradle APK build scratch space (data\exports),
    :: which is what actually needs the robocopy trick -- Gradle's nested
    :: build output paths exceed Windows' normal path length limit, so a
    :: plain "rmdir /s" fails on them. It gets recreated fresh on next use
    :: regardless. Deliberately NOT touching the rest of data\ (pgdata is
    :: the Postgres cluster -- Master/Engine Data, app_projects, model
    :: assets all live there; models/reference_images are uploaded assets).
    echo [2] Update mode - clearing APK build output only (exports)...
    if exist "D:\FlutterAI-App\FlutterAI\data\exports" (
        mkdir "%TEMP%\__pyi_empty__" >nul 2>&1
        robocopy "%TEMP%\__pyi_empty__" "D:\FlutterAI-App\FlutterAI\data\exports" /MIR /NFL /NDL /NJH /NJS /NC /NS /NP >nul 2>&1
        rmdir /s /q "D:\FlutterAI-App\FlutterAI\data\exports" >nul 2>&1
        rmdir /s /q "%TEMP%\__pyi_empty__" >nul 2>&1
        echo     Build exports cleared. Database and models kept.
    ) else (
        echo     Nothing to clear.
    )
    set "FLUTTERAI_FULL_REBUILD=0"
)

:: Pull latest code (whichever branch is currently checked out)
echo [3] Pulling latest code...
pushd "%~dp0..\.."
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD') do set "CURRENT_BRANCH=%%b"
echo     Branch: %CURRENT_BRANCH%
git pull origin %CURRENT_BRANCH%
if errorlevel 1 ( echo [ERROR] git pull failed - resolve conflicts manually, then re-run. & popd & exit /b 1 )
popd

:: Rebuild EXE. FLUTTERAI_FULL_REBUILD tells build.bat whether to preserve
:: data\ around the PyInstaller output wipe (0) or not (1) -- see build.bat.
echo [4] Rebuilding EXE...
call conda activate flutter-ai
cd /d "%~dp0"
call .\build.bat
if errorlevel 1 ( echo [ERROR] Build failed. & exit /b 1 )

:: Run
echo [5] Launching...
start "" "D:\FlutterAI-App\FlutterAI\flutterai.exe"
