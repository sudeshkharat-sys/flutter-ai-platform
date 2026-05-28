@echo off
setlocal EnableDelayedExpansion

echo ============================================================
echo  Flutter AI Studio - Windows EXE Build Script
echo ============================================================

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

set REACT_APP_API_URL=http://localhost:8001/api/v1
call npm run build
if errorlevel 1 ( echo [ERROR] React build failed. & exit /b 1 )
echo      Frontend built successfully.

:: --------------------------------------------------------------------------
:: Step 4 - Install Python build dependencies
:: --------------------------------------------------------------------------
echo.
echo [4/6] Installing Python build dependencies...
cd /d "%REPO_ROOT%"
pip install pyinstaller ^
    fastapi "uvicorn[standard]" "sqlalchemy[asyncio]" aiosqlite psycopg2-binary ^
    "celery[redis]" redis jinja2 ultralytics ^
    onnx onnxruntime onnx2tf tf_keras ^
    Pillow pyyaml python-dotenv ^
    pydantic pydantic-settings "passlib[bcrypt]" "python-jose[cryptography]" ^
    python-multipart aiofiles httpx
if errorlevel 1 ( echo [ERROR] pip install failed. & exit /b 1 )

:: --------------------------------------------------------------------------
:: Step 5 - Check for portable binaries and build tools
:: --------------------------------------------------------------------------
echo.
echo [5/6] Checking portable binaries and build tools...
cd /d "%DEPLOY_DIR%"

set MISSING=0

if exist "resources\postgres\bin\pg_ctl.exe" (
    echo      [OK] PostgreSQL portable found.
) else (
    echo      [MISS] Portable PostgreSQL NOT found.
    echo             Expected: %DEPLOY_DIR%resources\postgres\bin\pg_ctl.exe
    echo             Download: https://www.enterprisedb.com/download-postgresql-binaries
    set MISSING=1
)

if exist "resources\redis\redis-server.exe" (
    echo      [OK] Redis portable found.
) else (
    echo      [MISS] Portable Redis NOT found.
    echo             Expected: %DEPLOY_DIR%resources\redis\redis-server.exe
    echo             Download: https://github.com/microsoftarchive/redis/releases
    set MISSING=1
)

if exist "resources\flutter\bin\flutter.bat" (
    echo      [OK] Flutter SDK found.
) else (
    echo      [MISS] Flutter SDK NOT found.
    echo             Expected: %DEPLOY_DIR%resources\flutter\bin\flutter.bat
    echo             Download: https://docs.flutter.dev/release/archive (Windows ZIP)
    set MISSING=1
)

if exist "resources\jdk\bin\java.exe" (
    echo      [OK] JDK found.
) else (
    echo      [MISS] JDK NOT found.
    echo             Expected: %DEPLOY_DIR%resources\jdk\bin\java.exe
    echo             Download: https://adoptium.net/temurin/releases/?version=17 (Windows ZIP)
    set MISSING=1
)

if exist "resources\android-sdk\build-tools" (
    echo      [OK] Android SDK found.
) else (
    echo      [MISS] Android SDK NOT found.
    echo             Expected: %DEPLOY_DIR%resources\android-sdk\build-tools\
    echo             See SETUP_GUIDE.md for instructions.
    set MISSING=1
)

if exist "resources\gradle\gradle-8.10.2-all.zip" (
    echo      [OK] Gradle 8.10.2 ZIP found.
) else (
    echo      [MISS] Gradle ZIP NOT found.
    echo             Expected: %DEPLOY_DIR%resources\gradle\gradle-8.10.2-all.zip
    echo             Download: https://services.gradle.org/distributions/gradle-8.10.2-all.zip
    set MISSING=1
)

if "!MISSING!"=="1" (
    echo.
    set /p CONTINUE="Some resources are missing. Continue build anyway? [y/N]: "
    if /i "!CONTINUE!" neq "y" exit /b 1
)

:: --------------------------------------------------------------------------
:: Step 6 - Run PyInstaller
:: --------------------------------------------------------------------------
echo.
echo [6/6] Running PyInstaller...
cd /d "%DEPLOY_DIR%"

set "PYI_WORK=D:\flutterai-build-temp\work"
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
echo On first launch, the app will:
echo   - Verify all bundled build tools
echo   - Initialize the PostgreSQL database
echo   - Start all services
echo   - Open your browser at http://localhost:8001

endlocal
