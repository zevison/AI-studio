@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "tools\stop_local.py"
) else (
  where py.exe >nul 2>&1 && py -3 "tools\stop_local.py"
)
pause
