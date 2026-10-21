@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0apply_patch.ps1" -Target "%~1"
if errorlevel 1 (echo [FAILED] See message above.) else (echo [OK] Operation complete.)
echo.
pause
