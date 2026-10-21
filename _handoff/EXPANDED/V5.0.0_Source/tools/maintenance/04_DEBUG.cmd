@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" app.py --open-browser
) else (
  where py.exe >nul 2>&1
  if %ERRORLEVEL%==0 (py -3 app.py --open-browser) else (python app.py --open-browser)
)
echo.
echo The program exited. Keep this window open and send a screenshot of the error.
pause
