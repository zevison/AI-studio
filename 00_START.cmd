@echo off
setlocal EnableExtensions DisableDelayedExpansion
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
".venv\Scripts\python.exe" -c "import sys,struct;sys.exit(0 if sys.version_info[:2]==(3,12) and struct.calcsize('P')==8 else 1)"
if errorlevel 1 (
  echo [ERROR] Local Python is broken or incompatible. Run 01_INSTALL.cmd to repair it.
  pause
  exit /b 12
)
if not exist "%SystemRoot%\System32\wscript.exe" goto fallback
"%SystemRoot%\System32\wscript.exe" //nologo "%CD%\tools\launch_silent.vbs"
if not errorlevel 1 exit /b 0
:fallback
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%CD%\tools\launch_hidden.ps1"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%
