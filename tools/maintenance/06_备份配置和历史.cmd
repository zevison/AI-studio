@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" tools\backup_local.py
) else (
  where py.exe >nul 2>&1 && (py -3 tools\backup_local.py) || (python tools\backup_local.py)
)
