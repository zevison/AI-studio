@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."
if not exist ".venv\Scripts\python.exe" (
  echo Run 01_INSTALL.cmd first.
  pause
  exit /b 2
)
".venv\Scripts\python.exe" tools\migrate_old.py
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
