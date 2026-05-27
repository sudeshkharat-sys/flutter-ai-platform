# download_deps.ps1
# Downloads all external dependencies needed for the standalone EXE build.
# Run this ONCE before build_windows.bat.
# Requires: PowerShell 5+ and internet access.

param(
    [string]$DepsDir = "$PSScriptRoot\deps"
)

$ErrorActionPreference = "Stop"

function Download-File($url, $dest) {
    if (Test-Path $dest) {
        Write-Host "  [skip] $dest already exists"
        return
    }
    Write-Host "  Downloading $url ..."
    $tmp = "$dest.tmp"
    Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
    Move-Item $tmp $dest
    Write-Host "  Saved to $dest"
}

function Extract-Zip($zip, $dest) {
    Write-Host "  Extracting $zip -> $dest ..."
    if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $dest -Force
    Write-Host "  Done."
}

New-Item -ItemType Directory -Force -Path $DepsDir | Out-Null

# ── 1. Flutter SDK (Windows, stable) ─────────────────────────────────────────
Write-Host "`n[1/4] Flutter SDK"
$flutterZip  = "$DepsDir\flutter_windows.zip"
$flutterDest = "$DepsDir\flutter"
# Flutter 3.22 stable for Windows (update URL for newer version if needed)
$flutterUrl  = "https://storage.googleapis.com/flutter_infra_release/releases/stable/windows/flutter_windows_3.22.3-stable.zip"
Download-File $flutterUrl $flutterZip
Extract-Zip   $flutterZip $DepsDir
# The zip extracts a 'flutter' folder inside $DepsDir already

# ── 2. JDK 17 (Eclipse Temurin, portable ZIP) ────────────────────────────────
Write-Host "`n[2/4] JDK 17 (Eclipse Temurin)"
$jdkZip  = "$DepsDir\jdk17.zip"
$jdkDest = "$DepsDir\jdk"
$jdkUrl  = "https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.11%2B9/OpenJDK17U-jdk_x64_windows_hotspot_17.0.11_9.zip"
Download-File $jdkUrl $jdkZip
Extract-Zip   $jdkZip "$DepsDir\_jdk_raw"
# Rename the inner folder to 'jdk'
$inner = Get-ChildItem "$DepsDir\_jdk_raw" | Select-Object -First 1
Move-Item $inner.FullName $jdkDest
Remove-Item "$DepsDir\_jdk_raw" -Recurse -Force

# ── 3. Redis for Windows ─────────────────────────────────────────────────────
Write-Host "`n[3/4] Redis for Windows"
$redisZip  = "$DepsDir\redis.zip"
$redisDest = "$DepsDir\redis"
# tporadowski Redis 5.0.14.1 — stable Windows port
$redisUrl  = "https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.zip"
Download-File $redisUrl $redisZip
Extract-Zip   $redisZip $redisDest

# ── 4. PostgreSQL 15 (portable ZIP for Windows) ───────────────────────────────
Write-Host "`n[4/4] PostgreSQL 15 portable"
$pgZip  = "$DepsDir\postgresql.zip"
$pgDest = "$DepsDir\postgresql"
# EnterpriseDB portable binaries
$pgUrl  = "https://get.enterprisedb.com/postgresql/postgresql-15.7-1-windows-x64-binaries.zip"
Download-File $pgUrl $pgZip
Write-Host "  Extracting PostgreSQL (this may take a minute)..."
Extract-Zip $pgZip "$DepsDir\_pg_raw"
$pgInner = Get-ChildItem "$DepsDir\_pg_raw" | Select-Object -First 1
Move-Item $pgInner.FullName $pgDest
Remove-Item "$DepsDir\_pg_raw" -Recurse -Force

Write-Host "`n============================================"
Write-Host " All dependencies downloaded to: $DepsDir"
Write-Host " Run build_windows.bat next."
Write-Host "============================================`n"
