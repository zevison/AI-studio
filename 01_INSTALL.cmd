@echo off
chcp 65001 >nul
title Seedance Studio V5.2.0 - Setup
rem One-click Windows setup with verified progress steps
color 0B
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 (
  echo [ERROR] Cannot open installation folder.
  pause
  exit /b 10
)
if not exist "tools\install_new_pc.ps1" (
  echo [ERROR] Installation components missing. Re-extract the FULL ZIP package.
  pause
  exit /b 11
)
echo.
echo  ==============================================================
echo   SEEDANCE STUDIO  ^|  V5.2.0  ^|  FIRST-TIME INSTALL
 echo   Complete application + voice binding + video workflow
 echo  ==============================================================
echo.
echo   STEP 1 / 5  Detect or download Python 3.12
 echo   STEP 2 / 5  Prepare Python environment and packages
 echo   STEP 3 / 5  Check FFmpeg, video, speech and OCR
 echo   STEP 4 / 5  Verify runtime, timezone and optional tools
 echo   STEP 5 / 5  Save health report and finish
 echo.
echo   Progress represents verified setup stages, not download speed.
echo   Internet is required on a new computer. Existing settings are safe.
echo.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%CD%\tools\install_new_pc.ps1"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo [OK] Installation finished. Double-click 00_START.cmd to open the Studio.
) else (
  echo [ERROR] Setup did not finish. Error code: %RC%
  echo Read install_logs\python_setup_v426.log or dependency_report.json
)
echo.
pause
exit /b %RC%
