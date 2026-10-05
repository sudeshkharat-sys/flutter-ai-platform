@echo off
REM One-click local APK build. Uses the same Flutter/JDK/Android SDK locations as the
REM platform's inspection-app builder (backend/app/tasks/build_apk.py) when they exist.
cd /d "%~dp0"

if exist "C:\flutter\bin\flutter.bat" set "PATH=C:\flutter\bin;%PATH%"
if exist "C:\jdk-17.0.14+7" set "JAVA_HOME=C:\jdk-17.0.14+7"
if exist "C:\android-sdk" set "ANDROID_HOME=C:\android-sdk"
if defined JAVA_HOME set "PATH=%JAVA_HOME%\bin;%PATH%"

where flutter >nul 2>nul
if errorlevel 1 (
  echo Flutter not found. Looked on PATH and in C:\flutter\bin.
  echo Fix: set the path to YOUR flutter folder, then run this again, e.g.
  echo     set PATH=D:\tools\flutter\bin;%%PATH%%
  goto :err
)
echo Using Flutter: & where flutter
echo JAVA_HOME=%JAVA_HOME%  ANDROID_HOME=%ANDROID_HOME%

python setup_android.py || goto :err
call flutter pub get || goto :err
call flutter build apk --release --android-skip-build-dependency-validation || goto :err
echo.
echo APK: %~dp0build\app\outputs\flutter-apk\app-release.apk
pause
exit /b 0
:err
echo.
echo BUILD FAILED -- copy the text above and send it for fixing
pause
exit /b 1
