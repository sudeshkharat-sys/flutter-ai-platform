@echo off
setlocal enabledelayedexpansion
title Mahindra Digital Eye Vault - Viewer (auto-restart watchdog)

rem Restarts DigitalEyeViewer.exe automatically whenever it exits, for
rem any reason -- a crash, someone accidentally closing the window, the
rem PC waking from sleep, etc. Pairs with install_autostart.ps1, which
rem makes this .bat itself launch automatically at login so the viewer
rem comes back up on its own after a reboot too -- important since this
rem is the process other users' browsers actually connect to.
rem
rem This window IS the watchdog -- closing it (or ending its process in
rem Task Manager) stops the auto-restart and the viewer along with it.
rem Minimize it instead of closing it.

set "SCRIPT_DIR=%~dp0"
set "EXE=%SCRIPT_DIR%DigitalEyeViewer.exe"
set "LOG=%SCRIPT_DIR%viewer_watchdog.log"

if not exist "%EXE%" (
    echo [watchdog] DigitalEyeViewer.exe not found next to this .bat file.
    echo [watchdog] Expected: %EXE%
    echo [watchdog] Build it first ^(see README.md^) or copy this .bat next to the exe.
    pause
    exit /b 1
)

echo Mahindra Digital Eye Vault - Viewer watchdog
echo This window restarts DigitalEyeViewer.exe automatically if it ever closes or crashes.
echo Minimize this window to keep it running in the background -- do NOT close it.
echo Watchdog log: %LOG%
echo.

:loop
echo [%date% %time%] Starting DigitalEyeViewer.exe >> "%LOG%"
"%EXE%"
echo [%date% %time%] DigitalEyeViewer.exe exited ^(code %errorlevel%^) -- restarting in 5s >> "%LOG%"
echo.
echo [watchdog] DigitalEyeViewer.exe stopped -- restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
