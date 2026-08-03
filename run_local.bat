@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Al-Nathim - Local Run (with restart)
echo ============================================
echo.
echo [1/4] Stopping any old Al-Nathim servers on port 5000...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5000" ^| findstr LISTENING') do (
  echo   Killing PID %%p
  taskkill /f /pid %%p >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo [2/4] Starting fresh Flask app on http://localhost:5000 ...
start "AlNathim Flask" cmd /k py app.py

echo [3/4] Waiting for the server to become ready ...
for /l %%i in (1,1,40) do (
  curl -s -m 1 -o nul http://127.0.0.1:5000/login
  if not errorlevel 1 goto READY
  ping -n 2 127.0.0.1 >nul
)
echo [ERROR] The server did not start within 20 seconds.
echo Check that the Flask window did not crash.
goto END

:READY
echo [4/4] Server is ready - opening the app...
start "" http://localhost:5000/customers

:END
endlocal