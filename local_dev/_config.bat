@echo off
rem Shared paths for the local launchers. Edit here if folders move.
set "PROJ=D:\Codes\Dataeaze\flutter-ai-platform"
set "PGROOT=C:\postgresql-17.8-1-windows-x64-binaries\pgsql"
set "PGBIN=%PGROOT%\bin"
set "PGDATA=%PGROOT%\data"
set "PGLOG=C:\postgresql-17.8-1-windows-x64-binaries\pg.log"
set "REDISDIR=C:\redis-latest"
rem Miniconda root differs per machine. First one that exists wins.
set "CONDA_ACTIVATE="
if exist "C:\conda\Scripts\activate.bat" set "CONDA_ACTIVATE=C:\conda\Scripts\activate.bat"
if not defined CONDA_ACTIVATE if exist "%USERPROFILE%\miniconda3\Scripts\activate.bat" set "CONDA_ACTIVATE=%USERPROFILE%\miniconda3\Scripts\activate.bat"
if not defined CONDA_ACTIVATE if exist "%USERPROFILE%\anaconda3\Scripts\activate.bat" set "CONDA_ACTIVATE=%USERPROFILE%\anaconda3\Scripts\activate.bat"
