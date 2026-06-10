@echo off
setlocal

cd /d "%~dp0\.."

call scripts\stop_daypilot.bat
python --version >nul 2>nul
if errorlevel 1 (
  py -3 scripts\restore_db.py
) else (
  python scripts\restore_db.py
)
