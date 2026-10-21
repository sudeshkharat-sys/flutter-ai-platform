@echo off
REM One-click APK build for Runner Cam. If flutter is not on PATH, set it first, e.g.
REM     set PATH=D:\tools\flutter\bin;%PATH%
cd /d "%~dp0"
where flutter >nul 2>nul
if errorlevel 1 (
  if exist "C:\flutter\bin\flutter.bat" set "PATH=C:\flutter\bin;%PATH%"
)
where flutter >nul 2>nul
if errorlevel 1 (
  echo Flutter not found on PATH. Run:  set PATH=YOUR_FLUTTER_FOLDER\bin;%%PATH%%   then run this again.
  goto :err
)
python setup_android.py || goto :err
call flutter pub get || goto :err
call flutter build apk --release --android-skip-build-dependency-validation || goto :err
echo.
echo APK: %~dp0build\app\outputs\flutter-apk\app-release.apk
pause
exit /b 0
:err
echo BUILD FAILED -- copy the text above and send it for fixing
pause
exit /b 1
