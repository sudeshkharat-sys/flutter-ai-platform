@echo off
REM Builds dist\runner.exe (single file, no install needed on the target PC).
cd /d "%~dp0"
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu || goto :err
python -m pip install -r requirements.txt || goto :err
python -m PyInstaller --noconfirm runner.spec || goto :err
echo.
echo Built: %~dp0dist\runner.exe
echo Put runner_config.json (see runner_config.example.json) and your best.pt next to it, then double-click.
pause
exit /b 0
:err
echo BUILD FAILED
pause
exit /b 1
