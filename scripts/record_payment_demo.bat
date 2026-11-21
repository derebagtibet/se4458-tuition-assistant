@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
echo Checking the local stack (Kong gateway on localhost:8000)...
curl -s -o nul -w "Gateway /api/openapi.json -> HTTP %%{http_code}\n" http://localhost:8000/api/openapi.json
if errorlevel 1 (
  echo Gateway is not reachable. Start Docker Desktop and run: docker compose up -d
  pause
  exit /b 1
)
echo.
".venv\Scripts\python.exe" scripts\record_payment_demo.py
echo.
pause
