@echo off
rem No -W here: the password comes from setup-pgpass.bat (run it once).
call "%~dp0_config.bat"
"%PGBIN%\psql.exe" -U postgres
