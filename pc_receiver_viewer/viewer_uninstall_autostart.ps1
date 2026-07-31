# Removes the Scheduled Task created by viewer_install_autostart.ps1. Does not
# stop an already-running watchdog/viewer -- close the watchdog window
# (or its DigitalEyeViewer.exe / cmd.exe processes in Task Manager) for
# that.
#
# Usage: powershell -ExecutionPolicy Bypass -File viewer_uninstall_autostart.ps1

$ErrorActionPreference = "Stop"

$taskName = "Mahindra Digital Eye Vault - Viewer"

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "No scheduled task named '$taskName' found -- nothing to remove." -ForegroundColor Yellow
    exit 0
}

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Host "Scheduled task '$taskName' removed." -ForegroundColor Green
