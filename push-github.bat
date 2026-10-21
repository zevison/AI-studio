@echo off
REM Seedance Studio — 一键推 GitHub
REM 用法：push-github.bat <github-username> <repo-name>
REM 例：push-github.bat zhangsan SeedanceStudio

setlocal
set REPO=%1
set NAME=%2
if "%REPO%"=="" ( echo 用法: push-github.bat ^<github-username^> ^<repo-name^> & exit /b 1 )
if "%NAME%"==""  ( echo 用法: push-github.bat ^<github-username^> ^<repo-name^> & exit /b 1 )

set REPO_DIR=C:\Users\Administrator\Documents\Trae-CN\temp_20261021_01\SeedanceStudio

echo === Seedance Studio GitHub 推送 ===
echo 本地路径: %REPO_DIR%
echo 远程: https://github.com/%REPO%/%NAME%.git
echo.

cd /d "%REPO_DIR%"

REM 如果还没 init，初始化
if not exist .git (
    echo [1/4] git init ...
    git init
    git branch -M main
)

echo [2/4] git add 所有文件 ...
git add .

echo [3/4] git commit ...
git -c user.name="Seedance Studio" -c user.email="seedance@local" commit -m "feat: Seedance Studio 5.2.0 R3.18 initial source

- 应用核心: app.py (ThreadingHTTPServer), core/ (95 modules)
- 前端: static/ (HTML/JS), 使用指南 user-guide.html
- 依赖: requirements.txt, requirements-desktop.txt, tools/hypit_provider/node_modules
- 构建/验收: tools/build_desktop_exe.py, tools/dev_check.py
- 新增版本检查: core/version_check.py + /api/version/check 路由
- 托管版本文件: docs/latest-release.json

配置远程版本检查:
  .env 里加 VERSION_CHECK_URL=https://raw.githubusercontent.com/%REPO%/%NAME%/main/docs/latest-release.json

已在 .gitignore 中排除:
  密钥(.env, .dpapi, secrets.json), 数据库(*.db), 日志,
  node_modules, 运行时二进制(node.exe, chrome-headless, ffmpeg zip),
  构建产物(build/, installers/*.exe), 本地缓存" || echo (已有 commit，跳过)

echo [4/4] 配置远程并推送 ...
git remote remove origin 2>nul
git remote add origin https://github.com/%REPO%/%NAME%.git
echo.
echo ============================================
echo 推送前请确认:
echo   1. 你已在 GitHub 创建了仓库 %NAME% (Private 或 Public)
echo   2. 你已安装 GitHub Desktop 或配置好 git credential manager
echo.
echo 准备执行: git push -u origin main
echo (如遇认证问题，请用 GitHub Desktop push，或先运行 git config --global credential.helper manager)
echo ============================================
echo.
choice /c YN /m "现在推送? [Y=是 N=否]"
if errorlevel 2 ( echo 已跳过，稍后手动 push。 & exit /b 0 )

git push -u origin main
echo.
echo === 完成 ===
echo 你的仓库: https://github.com/%REPO%/%NAME%
echo 版本端点: https://raw.githubusercontent.com/%REPO%/%NAME%/main/docs/latest-release.json
echo.
echo 下一步:
echo   1. 每次发新版本，更新 docs/latest-release.json 里的 version/build/installer_url
echo   2. 在安装包目录生成新的 exe，推到 GitHub Releases
echo   3. 用户本地 .env 里加 VERSION_CHECK_URL=上面的 raw URL
echo   4. 用户启动时自动检查更新 + 右上角弹出提醒

endlocal
