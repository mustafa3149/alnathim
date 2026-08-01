@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Al-Nathim - Signal Scanner Agent (LAN)
echo ============================================
echo.
echo Make sure RELAY_URL (and AGENT_TOKEN) are set in your .env
echo so the tower PC relays signal batches to Render.
echo.
echo Stopping any previous agent instance...
taskkill /f /im python.exe /fi "WINDOWTITLE eq AlNathimAgent*" >nul 2>&1

echo Starting the agent. Keep this window open.
echo Press Ctrl+C to stop it.
echo.
start "AlNathimAgent" cmd /k "py -m snmp_monitor.agent"

echo Agent started in a new window.
endlocal