@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Al-Nathim - ISP Management System
echo ============================================
echo Starting Flask app on http://localhost:5000 ...
start "AlNathim Flask" cmd /k py app.py

echo Waiting for the server to become ready ...
for /l %%i in (1,1,40) do (
  curl -s -m 1 -o nul http://127.0.0.1:5000/login
  if not errorlevel 1 goto READY
  ping -n 2 127.0.0.1 >nul
)
echo [ERROR] The server did not start within 20 seconds.
echo Check that the Flask window did not crash.
goto END

:READY
echo [OK] Server is ready - opening the app...
start "" http://localhost:5000

:END
endlocal