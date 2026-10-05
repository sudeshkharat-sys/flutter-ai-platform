@echo off
REM Builds dist\runner.exe (single file, no install needed on the target PC).
cd /d "%~dp0"
python -m pip install -r requirements.txt || goto :err
python -m PyInstaller --noconfirm runner.spec || goto :err
echo.
echo Built: %~dp0dist\runner.exe  (copy that one file anywhere and double-click)
pause
exit /b 0
:err
echo BUILD FAILED
pause
exit /b 1
