@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if errorlevel 1 (
  echo [ERROR] Cannot open Seedance Studio folder.
  pause
  exit /b 10
)
if not exist "tools\launch_silent.vbs" (
  echo [ERROR] Launcher component missing. Please re-extract the FULL ZIP.
  pause
  exit /b 11
)
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Local Python environment missing. Please run 01_INSTALL.cmd first.
  pause
  exit /b 12
)
if not exist "%SystemRoot%\System32\wscript.exe" (
  echo [ERROR] Windows Script Host is unavailable. Check Windows system policy.
  pause
  exit /b 13
)
rem Start the existing version-aware hidden launcher; no persistent CMD window.
rem It will open the correct local workbench in the browser and reuse existing tasks.
"%SystemRoot%\System32\wscript.exe" //nologo "%CD%\tools\launch_silent.vbs"
exit /b %ERRORLEVEL%
