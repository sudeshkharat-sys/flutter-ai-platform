# Creates a Windows desktop shortcut for PCReceiver.exe, using the app's
# own icon (already embedded via --icon icon.ico at build time) so the
# shortcut on the desktop shows the Digital Eye logo, not a generic icon.
#
# Usage: run this from PowerShell in the same folder as PCReceiver.exe
#   powershell -ExecutionPolicy Bypass -File create_desktop_shortcut.ps1

$ErrorActionPreference = "Stop"

$exePath = Join-Path $PSScriptRoot "PCReceiver.exe"
if (-not (Test-Path $exePath)) {
    Write-Host "PCReceiver.exe not found in this folder. Build it first (see README.md), or run this script from the same folder as the exe." -ForegroundColor Red
    exit 1
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Mahindra Digital Eye Vault.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $exePath
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.IconLocation = $exePath  # pulls the icon baked into the exe
$shortcut.Description = "Mahindra Digital Eye Vault (PC Receiver)"
$shortcut.Save()

Write-Host "Desktop shortcut created: $shortcutPath" -ForegroundColor Green
