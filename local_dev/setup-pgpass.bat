@echo off
rem Run once. Saves the postgres password in YOUR Windows profile (not in the repo),
rem so psql and the backend tools stop asking for it.
set /p PGPW=Type your postgres password (it will be visible): 
if not exist "%APPDATA%\postgresql" mkdir "%APPDATA%\postgresql"
> "%APPDATA%\postgresql\pgpass.conf" echo localhost:5432:*:postgres:%PGPW%
echo Saved to %APPDATA%\postgresql\pgpass.conf
echo Test with: psql-pg.bat   (it should connect without asking).
