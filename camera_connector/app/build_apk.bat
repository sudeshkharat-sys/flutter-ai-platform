@echo off
REM One-click local APK build (no GitHub needed). Needs Flutter + Android SDK, same as the inspection app build.
cd /d "%~dp0"
python setup_android.py || goto :err
call flutter pub get || goto :err
call flutter build apk --release || goto :err
echo.
echo APK: %~dp0build\app\outputs\flutter-apk\app-release.apk
pause
exit /b 0
:err
echo BUILD FAILED -- copy the text above and send it for fixing
pause
exit /b 1
