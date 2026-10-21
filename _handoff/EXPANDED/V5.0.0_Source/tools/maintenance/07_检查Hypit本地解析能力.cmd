@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" tools\check_analysis.py
) else (
  echo [ERROR] .venv is missing. Run 01_INSTALL.cmd first.
  pause
  exit /b 1
)
