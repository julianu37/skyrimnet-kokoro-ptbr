@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
echo Starting SkyrimNet Kokoro PT-BR adapter on http://127.0.0.1:8020
".venv\Scripts\python.exe" server.py
pause
