@echo off
setlocal EnableDelayedExpansion

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
:: Two package sizes:
::   package_release.bat        - "update" package (default). Also skips
::                                 postgres\, redis\, flutter\, jdk\,
::                                 android-sdk\, gradle\ -- those are the
::                                 bundled build toolchain, several GB, and
::                                 never change between app updates. A
::                                 machine with an existing install already
::                                 has them, and extraction never deletes
::                                 what isn't in the zip, so leaving them
::                                 out just makes the update far smaller.
::   package_release.bat full   - "first install" package. Includes
::                                 everything (still minus data\/logs\),
::                                 for a machine that has nothing yet.
::
:: Run this AFTER build.bat has produced a fresh D:\FlutterAI-App\FlutterAI.
:: --------------------------------------------------------------------------

set "SRC=D:\FlutterAI-App\FlutterAI"
set "MODE=%~1"

if /i "%MODE%"=="full" (
    set "OUT=%~dp0FlutterAI-FirstInstall.zip"
) else (
    set "MODE=update"
    set "OUT=%~dp0FlutterAI-Update.zip"
)

echo ============================================================
echo  Flutter AI Studio - Package Release  (%MODE%)
echo ============================================================

if not exist "%SRC%\flutterai.exe" (
    echo [ERROR] %SRC%\flutterai.exe not found. Run build.bat first.
    exit /b 1
)

if exist "%OUT%" del /f /q "%OUT%"

if /i "%MODE%"=="full" (
    echo Staging files (excluding data\ and logs\ only)...
    powershell -NoProfile -Command ^
        "$ErrorActionPreference = 'Stop';" ^
        "$src = '%SRC%'; $out = '%OUT%';" ^
        "$staging = Join-Path $env:TEMP ('flutterai_pkg_' + [guid]::NewGuid());" ^
        "New-Item -ItemType Directory -Path $staging | Out-Null;" ^
        "Get-ChildItem -Path $src -Force | Where-Object { $_.Name -ne 'data' -and $_.Name -ne 'logs' } | Copy-Item -Destination $staging -Recurse -Force;" ^
        "Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $out -Force;" ^
        "Remove-Item -Recurse -Force $staging"
) else (
    echo Staging files (app code only -- excluding data\, logs\ and the bundled build toolchain)...
    powershell -NoProfile -Command ^
        "$ErrorActionPreference = 'Stop';" ^
        "$src = '%SRC%'; $out = '%OUT%';" ^
        "$skip = @('data','logs','postgres','redis','flutter','jdk','android-sdk','gradle');" ^
        "$staging = Join-Path $env:TEMP ('flutterai_pkg_' + [guid]::NewGuid());" ^
        "New-Item -ItemType Directory -Path $staging | Out-Null;" ^
        "Get-ChildItem -Path $src -Force | Where-Object { $skip -notcontains $_.Name } | Copy-Item -Destination $staging -Recurse -Force;" ^
        "Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $out -Force;" ^
        "Remove-Item -Recurse -Force $staging"
)
if errorlevel 1 ( echo [ERROR] Packaging failed. & exit /b 1 )

echo.
echo ============================================================
echo  PACKAGE COMPLETE
echo  Output: %OUT%
echo  Hand this zip + apply_update.bat to the target machine.
echo  It will NOT touch that machine's existing data.
if /i not "%MODE%"=="full" (
    echo  NOTE: this is an UPDATE package -- the target machine must
    echo  already have postgres\redis\flutter\jdk\android-sdk\gradle
    echo  from a previous install. Use "package_release.bat full" for
    echo  a machine that has nothing yet.
)
echo ============================================================

endlocal
