@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" tools\migrate_old.py
) else (
  where py.exe >nul 2>&1 && (py -3 tools\migrate_old.py) || (python tools\migrate_old.py)
)
