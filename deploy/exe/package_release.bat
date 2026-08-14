@echo off
setlocal

:: --------------------------------------------------------------------------
:: Packages the built app (D:\FlutterAI-App\FlutterAI) into a distributable
:: update zip, for handing off to a machine that already has an older
:: install. Deliberately excludes data\ and logs\ -- those hold the
:: recipient's own database, uploaded models and reference images, and
:: must never be part of an update package. apply_update.bat extracts this
:: zip on top of an existing install without deleting anything not present
:: in the zip, so an install's data\ folder is left completely alone as
:: long as this script never puts one in the zip to begin with.
::
:: Run this AFTER build.bat has produced a fresh D:\FlutterAI-App\FlutterAI.
:: --------------------------------------------------------------------------

set "SRC=D:\FlutterAI-App\FlutterAI"
set "OUT=%~dp0FlutterAI-Update.zip"

echo ============================================================
echo  Flutter AI Studio - Package Update
echo ============================================================

if not exist "%SRC%\flutterai.exe" (
    echo [ERROR] %SRC%\flutterai.exe not found. Run build.bat first.
    exit /b 1
)

if exist "%OUT%" del /f /q "%OUT%"

echo Staging files (excluding data\ and logs\)...
powershell -NoProfile -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$src = '%SRC%'; $out = '%OUT%';" ^
    "$staging = Join-Path $env:TEMP ('flutterai_pkg_' + [guid]::NewGuid());" ^
    "New-Item -ItemType Directory -Path $staging | Out-Null;" ^
    "Get-ChildItem -Path $src -Force | Where-Object { $_.Name -ne 'data' -and $_.Name -ne 'logs' } | Copy-Item -Destination $staging -Recurse -Force;" ^
    "Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $out -Force;" ^
    "Remove-Item -Recurse -Force $staging"
if errorlevel 1 ( echo [ERROR] Packaging failed. & exit /b 1 )

echo.
echo ============================================================
echo  PACKAGE COMPLETE
echo  Output: %OUT%
echo  Hand this zip + apply_update.bat to the target machine.
echo  It will NOT touch that machine's existing data.
echo ============================================================

endlocal
