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
echo.
echo  ZSCALER USERS: If pip/npm fail with SSL errors, either:
echo    a) Build on a non-Zscaler machine, then copy the EXE
echo    b) Add pip.conf / npm .cafile pointing to Zscaler cert
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
    if errorlevel 1 ( echo [ERROR] npm install failed. & exit /b 1 )
)

set REACT_APP_API_URL=http://localhost:8000/api/v1
set REACT_APP_BASE_URL=http://localhost:8000
call npm run build
if errorlevel 1 ( echo [ERROR] React build failed. & exit /b 1 )
echo      Frontend built successfully.

:: --------------------------------------------------------------------------
:: Step 4 - Install Python build dependencies
:: --------------------------------------------------------------------------
echo.
echo [4/6] Installing Python build dependencies...
cd /d "%REPO_ROOT%"

:: Install CPU-only PyTorch first to avoid downloading large CUDA packages.
:: If this URL is blocked by Zscaler, pre-install torch manually:
::   pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 (
    echo [WARNING] CPU torch install from PyTorch index failed.
    echo           Trying default PyPI (may pull in CUDA version)...
    pip install torch torchvision
)

pip install pyinstaller ^
    fastapi "uvicorn[standard]" "sqlalchemy[asyncio]" asyncpg psycopg2-binary aiosqlite ^
    "celery[redis]" redis "ultralytics==8.3.0" ^
    onnxruntime onnx onnxslim ^
    Pillow opencv-python-headless pyyaml python-dotenv ^
    pydantic pydantic-settings "passlib[bcrypt]" "python-jose[cryptography]" ^
    python-multipart websockets httpx aiofiles jinja2 loguru
if errorlevel 1 ( echo [ERROR] pip install failed. & exit /b 1 )

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
    echo   Download the binary ZIP from:
    echo   https://www.enterprisedb.com/download-postgresql-binaries
    echo   Extract bin\ lib\ share\ into: deploy\exe\resources\postgres\
    echo.
    set /p CONTINUE="Continue build without PostgreSQL binaries? [y/N]: "
    if /i "!CONTINUE!" neq "y" exit /b 1
)

if exist "resources\redis\redis-server.exe" (
    echo      Redis binary found.
) else (
    echo.
    echo [WARNING] Portable Redis NOT found.
    echo          Expected: %DEPLOY_DIR%resources\redis\redis-server.exe
    echo.
    echo   Download from: https://github.com/microsoftarchive/redis/releases
    echo   Place redis-server.exe and redis-cli.exe in: deploy\exe\resources\redis\
    echo.
    set /p CONTINUE="Continue build without Redis binary? [y/N]: "
    if /i "!CONTINUE!" neq "y" exit /b 1
)

:: --------------------------------------------------------------------------
:: Step 6 - Run PyInstaller
:: --------------------------------------------------------------------------
echo.
echo [6/6] Running PyInstaller...
cd /d "%DEPLOY_DIR%"

:: Build on D: drive to avoid C: space issues and Defender file locks.
set "PYI_TEMP=D:\flutterai-build-temp"
set "PYI_WORK=%PYI_TEMP%\work"
set "PYI_DIST=D:\FlutterAI-App"

echo      PyInstaller temp : %PYI_WORK%
echo      Final output     : %PYI_DIST%\FlutterAI\flutterai.exe

python -m PyInstaller launcher.spec --noconfirm ^
    --workpath "%PYI_WORK%" ^
    --distpath "%PYI_DIST%"
if errorlevel 1 ( echo [ERROR] PyInstaller failed. & exit /b 1 )

:: --------------------------------------------------------------------------
:: Done
:: --------------------------------------------------------------------------
echo.
echo ============================================================
echo  BUILD COMPLETE
echo  Output: D:\FlutterAI-App\FlutterAI\flutterai.exe
echo ============================================================
echo.
echo To run:
echo   cd D:\FlutterAI-App\FlutterAI
echo   flutterai.exe
echo.
echo On first launch the app will:
echo   - Initialize the PostgreSQL database (flutter_studio)
echo   - Start all services
echo   - Open your browser at http://localhost:8000
echo.
echo The folder D:\FlutterAI-App\FlutterAI is fully self-contained.
echo Copy it to any Windows machine - no install required.

endlocal
