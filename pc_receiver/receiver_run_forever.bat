@echo off
setlocal enabledelayedexpansion
title Mahindra Digital Eye Vault - Receiver (auto-restart watchdog)

rem Restarts PCReceiver.exe automatically whenever it exits, for any
rem reason -- a crash, someone accidentally closing the window, the PC
rem waking from sleep, etc. Pairs with receiver_install_autostart.ps1, which makes
rem this .bat itself launch automatically at login so the receiver comes
rem back up on its own after a reboot too.
rem
rem This window IS the watchdog -- closing it (or ending its process in
rem Task Manager) stops the auto-restart and the receiver along with it.
rem Minimize it instead of closing it.

set "SCRIPT_DIR=%~dp0"
set "EXE=%SCRIPT_DIR%PCReceiver.exe"
set "LOG=%SCRIPT_DIR%receiver_watchdog.log"
set "LOCK=%SCRIPT_DIR%receiver_watchdog.lock"

if not exist "%EXE%" (
    echo [watchdog] PCReceiver.exe not found next to this .bat file.
    echo [watchdog] Expected: %EXE%
    echo [watchdog] Build it first ^(see README.md^) or copy this .bat next to the exe.
    rem Deliberately a timed wait rather than `pause`: under the scheduled
    rem task this runs unattended, and a `pause` here would leave a window
    rem open forever waiting for a keypress nobody is there to give -- one
    rem more each time the task retries.
    timeout /t 20 /nobreak >nul
    exit /b 1
)

rem ── Single-instance guard ────────────────────────────────────────────
rem Holds an exclusive handle on a lock file for as long as this window
rem (and the exe it launches) lives. A second watchdog can't open that
rem same handle, so it reports it and exits instead of starting a rival
rem restart loop.
rem
rem Without this, every extra watchdog -- double-clicked again, or started
rem by the scheduled task on top of one already running manually --
rem launched its OWN PCReceiver.exe. They then fight over port 8765: one
rem wins, the rest die instantly with "only one usage of each socket
rem address", each restarts 5s later, crashes again, forever. That's the
rem endless "stopped -- restarting in 5 seconds" loop, and the multiple
rem windows. The "Access is denied." line mixed into it is this same
rem collision showing up on the log file: two windows appending to
rem receiver_watchdog.log at once, and whichever doesn't hold it fails
rem the redirect.
rem If the lock is already held, cmd prints its own "being used by another
rem process" line here and skips the block -- that message is left visible
rem on purpose rather than suppressed, since it names the real cause. Note
rem stderr is deliberately NOT redirected for the block, so PCReceiver.exe's
rem own error output still reaches this window normally.
set "GOTLOCK="
(
    set "GOTLOCK=1"
    call :watchdog
) 9>"%LOCK%"
if not defined GOTLOCK (
    echo.
    echo [watchdog] Another receiver watchdog is already running for this folder.
    echo [watchdog] Not starting a second one -- two of them fight over port 8765
    echo [watchdog] and neither stays up. Close the other watchdog window first
    echo [watchdog] if you want to restart it from here.
    echo [watchdog] ^(Lock file: %LOCK%^)
    echo.
    timeout /t 15 /nobreak >nul
    exit /b 1
)
exit /b 0

:watchdog
echo Mahindra Digital Eye Vault - Receiver watchdog
echo This window restarts PCReceiver.exe automatically if it ever closes or crashes.
echo Minimize this window to keep it running in the background -- do NOT close it.
echo Watchdog log: %LOG%
echo.

:loop
echo [%date% %time%] Starting PCReceiver.exe >> "%LOG%" 2>nul
"%EXE%"
echo [%date% %time%] PCReceiver.exe exited ^(code %errorlevel%^) -- restarting in 5s >> "%LOG%" 2>nul
echo.
echo [watchdog] PCReceiver.exe stopped -- restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
