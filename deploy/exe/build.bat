@echo off
setlocal EnableDelayedExpansion

echo ============================================================
echo  Flutter AI Studio - Windows EXE Build Script
echo  CPU-only build (safe for Zscaler / corporate laptops)
echo ============================================================
echo.
echo  NOTE: Run this script on a machine where pip/npm can reach
echo  the internet.  The resulting EXE is fully self-contained
echo  and requires NO internet access when deployed.
echo ============================================================

:: Resolve repo root (two levels up from deploy\exe)
set "DEPLOY_DIR=%~dp0"
pushd "%~dp0..\.."
set "REPO_ROOT=%CD%"
popd

echo [DEBUG] DEPLOY_DIR = %DEPLOY_DIR%
echo [DEBUG] REPO_ROOT  = %REPO_ROOT%

:: --------------------------------------------------------------------------
:: Step 1 - Check Python
:: --------------------------------------------------------------------------
echo.
echo [1/6] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.11 from https://www.python.org/downloads/
    exit /b 1
)
python --version

:: --------------------------------------------------------------------------
:: Step 2 - Check Node
:: --------------------------------------------------------------------------
echo.
echo [2/6] Checking Node.js / npm...
node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js not found. Install from https://nodejs.org/
    exit /b 1
)
node --version
call npm --version

:: --------------------------------------------------------------------------
:: Step 3 - Build React frontend
:: --------------------------------------------------------------------------
echo.
echo [3/6] Building React frontend...
cd /d "%REPO_ROOT%\frontend"

if not exist node_modules (
    echo      Installing npm packages...
    call npm install --legacy-peer-deps
    if errorlevel 1 (
        echo [ERROR] npm install failed.
        exit /b 1
    )
)

set REACT_APP_API_URL=http://localhost:8000/api/v1
set REACT_APP_BASE_URL=http://localhost:8000
call npm run build
if errorlevel 1 (
    echo [ERROR] React build failed.
    exit /b 1
)
echo      Frontend built successfully.

:: --------------------------------------------------------------------------
:: Step 4 - Install Python build dependencies
:: --------------------------------------------------------------------------
echo.
echo [4/6] Installing Python build dependencies...
cd /d "%REPO_ROOT%"

echo      Installing CPU-only PyTorch...
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 (
    echo [WARNING] CPU torch from PyTorch index failed, trying PyPI...
    pip install torch torchvision
)

echo      Installing remaining packages...
pip install -r "%DEPLOY_DIR%build_requirements.txt"
if errorlevel 1 (
    echo [ERROR] pip install failed.
    exit /b 1
)

:: --------------------------------------------------------------------------
:: Step 5 - Check for portable binaries
:: --------------------------------------------------------------------------
echo.
echo [5/6] Checking portable service binaries...
cd /d "%DEPLOY_DIR%"

if exist "resources\postgres\bin\pg_ctl.exe" (
    echo      PostgreSQL binaries found.
) else (
    echo.
    echo [WARNING] Portable PostgreSQL NOT found.
    echo          Expected: %DEPLOY_DIR%resources\postgres\bin\pg_ctl.exe
    echo.
    echo   Download from: https://www.enterprisedb.com/download-postgresql-binaries
    echo   Extract bin\ lib\ share\ into: deploy\exe\resources\postgres\
    echo.
    set /p CONTINUE=Continue without PostgreSQL binaries? [y/N]:
    if /i "!CONTINUE!" neq "y" exit /b 1
)

if exist "resources\redis\redis-server.exe" (
    echo      Redis binary found.
) else (
    echo.
    echo [WARNING] Portable Redis NOT found.
    echo          Expected: %DEPLOY_DIR%resources\redis\redis-server.exe
    echo.
    echo   Download from: https://github.com/tporadowski/redis/releases
    echo   Place redis-server.exe and redis-cli.exe in: deploy\exe\resources\redis\
    echo.
    set /p CONTINUE=Continue without Redis binary? [y/N]:
    if /i "!CONTINUE!" neq "y" exit /b 1
)

:: --------------------------------------------------------------------------
:: Step 6 - Run PyInstaller  (output shown live AND saved to build_log.txt)
:: --------------------------------------------------------------------------
echo.
echo [6/6] Running PyInstaller...
cd /d "%DEPLOY_DIR%"

set "PYI_WORK=C:\flutterai-build-temp\work"
set "PYI_DIST=C:\FlutterAI-App"
set "BUILD_LOG=%DEPLOY_DIR%build_log.txt"

echo      Output:   %PYI_DIST%\FlutterAI\flutterai.exe
echo      Build log: %BUILD_LOG%
echo.

:: PowerShell Tee-Object mirrors output to screen AND saves to UTF-8 file.
powershell -ExecutionPolicy Bypass -Command ^
  "$w='%PYI_WORK%'; $d='%PYI_DIST%'; $l='%BUILD_LOG%';" ^
  "& python -m PyInstaller launcher.spec --noconfirm --workpath $w --distpath $d 2>&1 | Tee-Object -FilePath $l -Encoding UTF8"

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller failed.
    echo.
    echo ---- Last 60 lines of build log ----
    powershell -Command "Get-Content '%BUILD_LOG%' -Encoding UTF8 | Select-Object -Last 60"
    echo ---- Full log saved to: %BUILD_LOG% ----
    exit /b 1
)

:: Always print a filtered summary of warnings after a successful build too
echo.
echo ---- Build warnings summary (missing modules etc.) ----
powershell -Command "Get-Content '%BUILD_LOG%' -Encoding UTF8 | Select-String 'WARNING.*module|not found|missing|No module' | Select-Object -Last 40"
echo ---- Full log: %BUILD_LOG% ----

:: --------------------------------------------------------------------------
echo.
echo ============================================================
echo  BUILD COMPLETE
echo  Output: C:\FlutterAI-App\FlutterAI\flutterai.exe
echo ============================================================
echo.
echo To run:
echo   cd C:\FlutterAI-App\FlutterAI
echo   flutterai.exe

endlocal
