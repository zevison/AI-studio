@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."
set /p "OLD=请将旧版工作台文件夹拖入窗口并按回车（保留已配置密钥与历史）： "
if "%OLD%"=="" exit /b 0
set "OLD=%OLD:"=%"
where py >nul 2>&1 && (py -3 tools\migrate_complete.py "%OLD%" & goto end)
python tools\migrate_complete.py "%OLD%"
:end
pause
