@echo off
setlocal enabledelayedexpansion
title Mahindra Digital Eye Vault - Receiver (auto-restart watchdog)

rem Restarts PCReceiver.exe automatically whenever it exits, for any
rem reason -- a crash, someone accidentally closing the window, the PC
rem waking from sleep, etc. Pairs with install_autostart.ps1, which makes
rem this .bat itself launch automatically at login so the receiver comes
rem back up on its own after a reboot too.
rem
rem This window IS the watchdog -- closing it (or ending its process in
rem Task Manager) stops the auto-restart and the receiver along with it.
rem Minimize it instead of closing it.

set "SCRIPT_DIR=%~dp0"
set "EXE=%SCRIPT_DIR%PCReceiver.exe"
set "LOG=%SCRIPT_DIR%receiver_watchdog.log"

if not exist "%EXE%" (
    echo [watchdog] PCReceiver.exe not found next to this .bat file.
    echo [watchdog] Expected: %EXE%
    echo [watchdog] Build it first ^(see README.md^) or copy this .bat next to the exe.
    pause
    exit /b 1
)

echo Mahindra Digital Eye Vault - Receiver watchdog
echo This window restarts PCReceiver.exe automatically if it ever closes or crashes.
echo Minimize this window to keep it running in the background -- do NOT close it.
echo Watchdog log: %LOG%
echo.

:loop
echo [%date% %time%] Starting PCReceiver.exe >> "%LOG%"
"%EXE%"
echo [%date% %time%] PCReceiver.exe exited ^(code %errorlevel%^) -- restarting in 5s >> "%LOG%"
echo.
echo [watchdog] PCReceiver.exe stopped -- restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
