@echo off
REM ============================================================
REM  build_windows.bat  — Full build for FlutterAIStudio EXE
REM  Run this from the exe-build\ folder on a Windows machine.
REM
REM  Prerequisites:
REM    - Python 3.11+ on PATH
REM    - Run download_deps.ps1 first (downloads Flutter/JDK/Redis/PG)
REM    - Inno Setup 6 installed at default location
REM    - Node.js on PATH (for React build)
REM ============================================================

setlocal EnableDelayedExpansion

set SCRIPT_DIR=%~dp0
set ROOT_DIR=%SCRIPT_DIR%..
set DEPS_DIR=%SCRIPT_DIR%deps
set DIST_DIR=%SCRIPT_DIR%dist
set BUILD_DIR=%SCRIPT_DIR%build_tmp

echo.
echo ============================================================
echo   FlutterAIStudio Windows EXE Builder
echo ============================================================
echo.

REM ── Verify deps exist ─────────────────────────────────────────────────────
if not exist "%DEPS_DIR%\flutter\bin\flutter.bat" (
    echo [ERROR] Flutter SDK not found at %DEPS_DIR%\flutter
    echo         Run download_deps.ps1 first.
    pause & exit /b 1
)
if not exist "%DEPS_DIR%\jdk\bin\java.exe" (
    echo [ERROR] JDK not found at %DEPS_DIR%\jdk
    echo         Run download_deps.ps1 first.
    pause & exit /b 1
)
if not exist "%DEPS_DIR%\redis\redis-server.exe" (
    echo [ERROR] Redis not found at %DEPS_DIR%\redis
    echo         Run download_deps.ps1 first.
    pause & exit /b 1
)
if not exist "%DEPS_DIR%\postgresql\bin\postgres.exe" (
    echo [ERROR] PostgreSQL not found at %DEPS_DIR%\postgresql
    echo         Run download_deps.ps1 first.
    pause & exit /b 1
)

REM ── Step 1: Build React frontend ─────────────────────────────────────────
echo [1/5] Building React frontend...
cd /d "%ROOT_DIR%\frontend"
call npm install --silent
if errorlevel 1 ( echo [ERROR] npm install failed & pause & exit /b 1 )
call npm run build
if errorlevel 1 ( echo [ERROR] npm build failed & pause & exit /b 1 )
echo       React build complete.

REM ── Step 2: Install Python dependencies ─────────────────────────────────
echo [2/5] Installing Python dependencies...
cd /d "%ROOT_DIR%\backend"
python -m pip install -r requirements.txt --quiet
if errorlevel 1 ( echo [ERROR] pip install failed & pause & exit /b 1 )
python -m pip install pyinstaller --quiet
if errorlevel 1 ( echo [ERROR] pyinstaller install failed & pause & exit /b 1 )
echo       Python dependencies installed.

REM ── Step 3: Run PyInstaller ──────────────────────────────────────────────
echo [3/5] Running PyInstaller (this takes several minutes)...
cd /d "%ROOT_DIR%"
python -m PyInstaller exe-build\FlutterAIStudio.spec --noconfirm --clean
if errorlevel 1 ( echo [ERROR] PyInstaller failed & pause & exit /b 1 )
echo       PyInstaller complete.

REM ── Step 4: Assemble installer staging directory ─────────────────────────
echo [4/5] Assembling installer package...
set STAGE=%BUILD_DIR%\FlutterAIStudio
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%"

REM Copy PyInstaller output
xcopy /e /i /q "%ROOT_DIR%\dist\FlutterAIStudio\*" "%STAGE%\"

REM Copy external deps
xcopy /e /i /q "%DEPS_DIR%\flutter"    "%STAGE%\flutter\"
xcopy /e /i /q "%DEPS_DIR%\jdk"        "%STAGE%\jdk\"
mkdir "%STAGE%\bin\redis"
mkdir "%STAGE%\bin\postgresql"
xcopy /e /i /q "%DEPS_DIR%\redis\*"      "%STAGE%\bin\redis\"
xcopy /e /i /q "%DEPS_DIR%\postgresql\*" "%STAGE%\bin\postgresql\"

REM Copy React build as frontend_build
xcopy /e /i /q "%ROOT_DIR%\frontend\build\*" "%STAGE%\frontend_build\"

REM Create empty data dirs
mkdir "%STAGE%\data\models"
mkdir "%STAGE%\data\exports"
mkdir "%STAGE%\data\reference_images"

REM Copy redis config
copy /y "%SCRIPT_DIR%redis.conf" "%STAGE%\bin\redis\redis.conf" >nul 2>&1

echo       Assembly complete.

REM ── Step 5: Build Inno Setup installer ──────────────────────────────────
echo [5/5] Building installer with Inno Setup...
set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist %ISCC% (
    echo [WARN] Inno Setup not found at default path.
    echo        Skipping installer creation. Distribute the folder:
    echo        %STAGE%
) else (
    %ISCC% "%SCRIPT_DIR%setup.iss"
    if errorlevel 1 ( echo [ERROR] Inno Setup failed & pause & exit /b 1 )
    echo       Installer created in: %SCRIPT_DIR%dist\
)

echo.
echo ============================================================
echo   BUILD COMPLETE
if exist "%SCRIPT_DIR%dist\FlutterAIStudio_Setup.exe" (
    echo   Installer: %SCRIPT_DIR%dist\FlutterAIStudio_Setup.exe
) else (
    echo   Folder:    %STAGE%
)
echo ============================================================
echo.
pause
