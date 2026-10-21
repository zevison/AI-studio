$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
Set-Location "C:\Users\Administrator\Documents\Trae-CN\temp_20261021_01\SeedanceStudio"

if (-not (Test-Path ".git")) {
    Write-Output "[1/4] git init + main branch"
    git init
    git branch -M main
}

Write-Output "[2/4] git add ."
git add .

Write-Output "[3/4] git commit"
git -c user.name="Seedance Studio" -c user.email="seedance@local" commit -m "feat: Seedance Studio 5.2.0 R3.18 initial source

- app.py (ThreadingHTTPServer), core/ (95 modules), static/ (HTML/JS frontend)
- docs/latest-release.json version endpoint for remote check
- core/version_check.py + /api/version/check route
- Frontend startup toast reminder when newer version is available
- .env.example: VERSION_CHECK_URL setting"

Write-Output ""
Write-Output "[4/4] Status:"
git status --short | Select-Object -First 20
Write-Output ""
Write-Output "=== 本地初始化完成 ==="
Write-Output "接下来：在 GitHub 创建仓库后，跑 push-github.bat <username> <repo>"
