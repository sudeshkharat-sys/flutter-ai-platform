@echo off
echo ============================================================
echo  Flutter AI Studio - Restart
echo ============================================================

echo [1] Stopping all processes...
taskkill /f /im flutterai.exe >nul 2>&1
taskkill /f /im postgres.exe >nul 2>&1
taskkill /f /im redis-server.exe >nul 2>&1
taskkill /f /im java.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2] Clearing runtime data...
if exist "D:\FlutterAI-App\FlutterAI\data" (
    mkdir "%TEMP%\__pyi_empty__" >nul 2>&1
    robocopy "%TEMP%\__pyi_empty__" "D:\FlutterAI-App\FlutterAI\data" /MIR /NFL /NDL /NJH /NJS /NC /NS /NP >nul 2>&1
    rmdir /s /q "D:\FlutterAI-App\FlutterAI\data" >nul 2>&1
    rmdir /s /q "%TEMP%\__pyi_empty__" >nul 2>&1
    echo     Data cleared.
) else (
    echo     Nothing to clear.
)

echo [3] Launching...
start "" "D:\FlutterAI-App\FlutterAI\flutterai.exe"
echo Done.
