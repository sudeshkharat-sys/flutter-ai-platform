@echo off
call "%~dp0_config.bat"
call "%CONDA_ACTIVATE%" flutter-ai
cd /d "%PROJ%"
pip install -r backend\requirements.txt
