@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."
set "PYEXE="
if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"
if not defined PYEXE where py.exe >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE where python.exe >nul 2>&1 && set "PYEXE=python"
if not defined PYEXE (
  echo [ERROR] Python 3 was not found. Run 01_INSTALL.cmd after installing Python 3.10+.
  pause
  exit /b 1
)
%PYEXE% tools\check_env.py
