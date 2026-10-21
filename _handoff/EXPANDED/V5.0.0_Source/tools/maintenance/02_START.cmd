@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Run 01_INSTALL.cmd to create the local Python environment.
  pause
  exit /b 1
)
wscript.exe //B "%~dp0..\launch_silent.vbs"
exit /b 0
