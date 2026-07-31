# Registers a Scheduled Task that launches receiver_run_forever.bat automatically
# every time this Windows account logs in -- so the receiver comes back up
# on its own after a reboot or logoff/logon, instead of relying on someone
# remembering to double-click the exe again. receiver_run_forever.bat itself already
# restarts PCReceiver.exe if it crashes; this task's own restart settings
# are a backstop in case the watchdog window itself gets killed.
#
# Needs a graphical logon session (it opens a browser tab on start), so
# this uses an "at logon" trigger rather than a headless "at system
# startup" service -- run it under whichever Windows account will normally
# be logged into this PC.
#
# Usage: run this from an elevated PowerShell in this folder
#   powershell -ExecutionPolicy Bypass -File receiver_install_autostart.ps1
# Safe to re-run any time to update the task. See receiver_uninstall_autostart.ps1
# to remove it.

$ErrorActionPreference = "Stop"

$taskName = "Mahindra Digital Eye Vault - Receiver"
$batPath = Join-Path $PSScriptRoot "receiver_run_forever.bat"

if (-not (Test-Path $batPath)) {
    Write-Host "receiver_run_forever.bat not found in this folder ($PSScriptRoot). Keep this script next to it." -ForegroundColor Red
    exit 1
}

$action = New-ScheduledTaskAction -Execute $batPath -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "Scheduled task '$taskName' installed." -ForegroundColor Green
Write-Host "The receiver will now start automatically the next time this account logs in." -ForegroundColor Green
Write-Host ""
Write-Host "To start it right now without logging out/in, run:" -ForegroundColor Yellow
Write-Host "  schtasks /run /tn `"$taskName`""
