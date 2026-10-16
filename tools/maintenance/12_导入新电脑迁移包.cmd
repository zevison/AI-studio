@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "tools\migration_bundle.py" import
) else (
  py -3 "tools\migration_bundle.py" import
)
pause
