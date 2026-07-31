# Registers a Scheduled Task that launches viewer_run_forever.bat automatically
# every time this Windows account logs in -- so the viewer (the server
# other users' browsers connect to) comes back up on its own after a
# reboot or logoff/logon. viewer_run_forever.bat itself already restarts
# DigitalEyeViewer.exe if it crashes; this task's own restart settings are
# a backstop in case the watchdog window itself gets killed.
#
# Uses an "at logon" trigger rather than a headless "at system startup"
# service -- run it under whichever Windows account will normally be
# logged into this PC.
#
# Usage: run this from an elevated PowerShell in this folder
#   powershell -ExecutionPolicy Bypass -File viewer_install_autostart.ps1
# Safe to re-run any time to update the task. See viewer_uninstall_autostart.ps1
# to remove it.

$ErrorActionPreference = "Stop"

$taskName = "Mahindra Digital Eye Vault - Viewer"
$batPath = Join-Path $PSScriptRoot "viewer_run_forever.bat"

if (-not (Test-Path $batPath)) {
    Write-Host "viewer_run_forever.bat not found in this folder ($PSScriptRoot). Keep this script next to it." -ForegroundColor Red
    exit 1
}

$action = New-ScheduledTaskAction -Execute $batPath -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "Scheduled task '$taskName' installed." -ForegroundColor Green
Write-Host "The viewer will now start automatically the next time this account logs in." -ForegroundColor Green
Write-Host ""
Write-Host "To start it right now without logging out/in, run:" -ForegroundColor Yellow
Write-Host "  schtasks /run /tn `"$taskName`""
