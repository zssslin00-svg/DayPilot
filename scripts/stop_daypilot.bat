@echo off
setlocal

cd /d "%~dp0\.."

python --version >nul 2>nul
if errorlevel 1 (
  py -3 scripts\stop_daypilot.py %*
) else (
  python scripts\stop_daypilot.py %*
)
