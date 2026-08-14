@echo off
setlocal EnableDelayedExpansion

:: --------------------------------------------------------------------------
:: Applies a FlutterAI-Update.zip (produced by package_release.bat) on top
:: of an existing install. Extraction only overwrites files present in the
:: zip -- it never deletes anything else -- and package_release.bat never
:: puts data\ or logs\ in the zip, so this cannot touch or delete the
:: install's existing database, models or reference images.
:: --------------------------------------------------------------------------

set "INSTALL_DIR=D:\FlutterAI-App\FlutterAI"

echo ============================================================
echo  Flutter AI Studio - Apply Update
echo ============================================================
echo  Install folder: %INSTALL_DIR%
echo.

set "ZIP_PATH=%~1"
if "%ZIP_PATH%"=="" (
    set /p ZIP_PATH="Path to FlutterAI-Update.zip: "
)
if not exist "%ZIP_PATH%" (
    echo [ERROR] File not found: %ZIP_PATH%
    exit /b 1
)

echo [1/3] Stopping FlutterAI if running...
taskkill /f /im flutterai.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2/3] Applying update (your data\ folder is not touched)...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
powershell -NoProfile -Command ^
    "Expand-Archive -Path '%ZIP_PATH%' -DestinationPath '%INSTALL_DIR%' -Force"
if errorlevel 1 ( echo [ERROR] Update extraction failed. & exit /b 1 )

echo [3/3] Launching updated app...
start "" "%INSTALL_DIR%\flutterai.exe"

echo.
echo ============================================================
echo  UPDATE APPLIED
echo ============================================================

endlocal
