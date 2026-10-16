@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0..\.."
if not exist ".venv\Scripts\python.exe" (
 echo [ERROR] Studio Python not installed.
 pause
 exit /b 2
)
".venv\Scripts\python.exe" tools\check_bright_downloader.py
pause
