@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
if errorlevel 1 exit /b 10
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%CD%\tools\accept_new_pc.ps1" -Phase Install
set "ACCEPTANCE_RC=%ERRORLEVEL%"
echo.
echo Acceptance finished. Send the result ZIP in this folder to the developer.
pause
exit /b %ACCEPTANCE_RC%
