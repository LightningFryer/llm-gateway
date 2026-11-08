@echo off
echo ========================================================
echo Starting Relay LLM Gateway and Next.js Dashboard
echo ========================================================

:: 1. Launch Gateway in new window
echo [1/2] Starting Gateway backend on http://localhost:8000 ...
start "Relay Gateway (Port 8000)" cmd /k "set PYTHONPATH=backend\src && .venv\Scripts\uvicorn relay.main:create_app --factory --host 127.0.0.1 --port 8000"

:: 2. Launch Dashboard in new window
echo [2/2] Starting Next.js Dashboard on http://localhost:3000 ...
start "Relay Dashboard (Port 3000)" cmd /k "cd dashboard && npm run dev"

echo.
echo All services launching!
echo - Gateway Backend: http://localhost:8000
echo - Control Dashboard: http://localhost:3000
echo.
echo Opening browser in 3 seconds...
timeout /t 3 >nul
start http://localhost:3000

