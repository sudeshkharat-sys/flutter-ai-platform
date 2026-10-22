@echo off
REM Builds dist\pc_runner.exe -- one file, copy anywhere, double-click, no install.
cd /d "%~dp0"
REM CPU-only torch first: the default (CUDA) wheel makes the exe ~2 GB
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu || goto :err
python -m pip install -r requirements.txt || goto :err
python -m PyInstaller --noconfirm connector.spec || goto :err
echo.
echo Built: %~dp0dist\pc_runner.exe
pause
exit /b 0
:err
echo BUILD FAILED -- copy the text above and send it for fixing
pause
exit /b 1
