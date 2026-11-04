# flutter-ai-platform PowerShell commands. Load from your profile with ONE line:
#   . "D:\Codes\Dataeaze\flutter-ai-platform\local_dev\ps-commands.ps1"
# Needs PowerShell scripts to be allowed. If your profile loads, this loads too.
$global:FALocal = $PSScriptRoot

function Start-FAPostgres   { & "$global:FALocal\start-postgres.bat" }
function Stop-FAPostgres    { & "$global:FALocal\stop-postgres.bat" }
function Connect-FAPostgres { & "$global:FALocal\psql-pg.bat" }

function Invoke-FAWindow([string]$Title, [string]$Bat) {
    Start-Process cmd.exe -ArgumentList '/k', "title $Title && `"$global:FALocal\$Bat.bat`""
}
function Start-FARedis    { Invoke-FAWindow 'FA Redis'    'start-redis' }
function Start-FABackend  { Invoke-FAWindow 'FA Backend'  'start-backend' }
function Start-FACelery   { Invoke-FAWindow 'FA Celery'   'start-celery' }
function Start-FAFrontend { Invoke-FAWindow 'FA Frontend' 'start-frontend' }

function Start-FAAll {
    Start-FAPostgres
    Start-FARedis
    Start-Sleep -Seconds 3
    Start-FABackend
    Start-FACelery
    Start-FAFrontend
}

function Show-FACommands {
    Write-Host ''
    Write-Host 'FLUTTER-AI COMMANDS READY:' -ForegroundColor Cyan
    Write-Host '  Start-FAPostgres / Stop-FAPostgres / Connect-FAPostgres' -ForegroundColor Yellow
    Write-Host '  Start-FARedis' -ForegroundColor Yellow
    Write-Host '  Start-FABackend / Start-FACelery / Start-FAFrontend' -ForegroundColor Yellow
    Write-Host '  Start-FAAll   (everything, each in its own window)' -ForegroundColor Yellow
    Write-Host ''
}
Show-FACommands
