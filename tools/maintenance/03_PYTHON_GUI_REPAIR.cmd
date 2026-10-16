@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\python_gui_repair.ps1"
echo.
echo When Python installation finishes, re-run 01_INSTALL.cmd.
pause
