@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\install_new_pc.ps1" -DiagnoseOnly
echo.
echo See install_logs\python_setup_v426.log for details.
pause
