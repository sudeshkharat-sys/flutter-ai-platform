@echo off
REM Builds dist\runner_connector.exe -- one file, copy anywhere, double-click, no install.
cd /d "%~dp0"
python -m pip install -r requirements.txt || goto :err
python -m PyInstaller --noconfirm connector.spec || goto :err
echo.
echo Built: %~dp0dist\runner_connector.exe
pause
exit /b 0
:err
echo BUILD FAILED -- copy the text above and send it for fixing
pause
exit /b 1
