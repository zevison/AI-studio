#requires -Version 5.1
param([ValidateSet('Install','AfterRestart','CheckPacket')][string]$Phase='Install')
$ErrorActionPreference='Stop'
$utf8=New-Object System.Text.UTF8Encoding($false)
try {[Console]::OutputEncoding=$utf8;$OutputEncoding=$utf8} catch {}
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
$packet=Split-Path -Parent $PSScriptRoot
$app=Join-Path $packet 'Studio'
$diag=Join-Path $packet '验收结果'
function Protect-DiagnosticText([string]$text) {
    $text=[regex]::Replace($text,'(?i)(Bearer\s+)[^\s"''<>]+','$1[REDACTED]')
    $text=[regex]::Replace($text,'(?i)\bsk-[a-z0-9_-]{8,}','[REDACTED]')
    $text=[regex]::Replace($text,'(?im)((?:api[_-]?key|access[_-]?key(?:[_-]?secret|[_-]?id)?|read[_-]?token|password|authorization|secret)["'']?\s*[=:]\s*)[^\r\n]+','$1[REDACTED]')
    $text=[regex]::Replace($text,'(?i)([?&](?:x-oss-[a-z-]+|OSSAccessKeyId|Signature|Expires|token|key)=)[^\s&"''<>]+','$1[REDACTED]')
    return $text
}
function Assert-Payload {
    $manifest=Get-Content -LiteralPath (Join-Path $packet 'payload-sha256.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if(-not $manifest -or @($manifest).Count -lt 1){throw 'Empty payload manifest'}
    $prefix=[IO.Path]::GetFullPath($app).TrimEnd('\')+'\'
    foreach($entry in $manifest) {
        $path=[IO.Path]::GetFullPath((Join-Path $app $entry.path))
        if(-not $path.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe payload manifest path'}
        if(-not (Test-Path -LiteralPath $path -PathType Leaf)){throw ('Missing package file: '+$entry.path)}
        if((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.sha256){throw ('Package file damaged: '+$entry.path+'. Re-extract the original complete ZIP.')}
    }
}
function Get-EnvironmentReport {
    $os=Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    $build=[int]$os.CurrentBuild
    $arch=$env:PROCESSOR_ARCHITECTURE
    if($env:PROCESSOR_ARCHITEW6432){$arch=$env:PROCESSOR_ARCHITEW6432}
    $name='Other Windows'
    if($build -ge 22000){$name='Windows 11'}elseif($build -ge 10240){$name='Windows 10'}
    $boot=$null
    try {$boot=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')} catch {}
    $tools=@{};$aliases=@{}
    foreach($tool in @('python.exe','py.exe','node.exe','ffmpeg.exe','ffprobe.exe')) {
        $found=Get-Command $tool -ErrorAction SilentlyContinue
        $isAlias=($found -and $found.Source -match '\\WindowsApps\\')
        $tools[$tool]=[bool]($found -and -not $isAlias)
        $aliases[$tool]=[bool]$isAlias
    }
    return @{windows=$name;edition=$os.EditionID;display_version=$os.DisplayVersion;build=$build;architecture=$arch;boot_time=$boot;preexisting_tools=$tools;windows_app_aliases=$aliases;studio_profile_present=(Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'SeedanceStudio'));local_venv_present=(Test-Path -LiteralPath (Join-Path $app '.venv'));local_env_present=(Test-Path -LiteralPath (Join-Path $app '.env'));local_database_present=(Test-Path -LiteralPath (Join-Path $app 'data/studio.db'));package_path=$app;phase=$Phase}
}
function Select-AcceptanceContext($environment) {
    $prior=Join-Path $diag 'first-environment.json'
    if(Test-Path -LiteralPath $prior) {
        $first=Get-Content -LiteralPath $prior -Raw -Encoding UTF8 | ConvertFrom-Json
        if($first.acceptance_context_version -ne 3 -or $first.package_path -ne $app){throw '首次记录与本验收版本/目录不一致，请保留日志并使用新解压目录。'}
        $mode=$first.profile_mode
        if($mode -notin @('isolated-existing-profile','normal-clean-profile')){throw '未知验收配置模式'}
    } else {
        if($Phase -eq 'AfterRestart'){throw '第一轮尚未建立安装记录，请先运行01；若01曾失败，请回传结果。'}
        if($environment.local_venv_present -or $environment.local_env_present -or $environment.local_database_present){throw '此应用目录已有未归属本轮的环境/数据；不会覆盖，请使用新解压目录。'}
        $mode='normal-clean-profile'
        if($environment.studio_profile_present){$mode='isolated-existing-profile'}
        $environment.acceptance_context_version=3
        $environment.profile_mode=$mode
        if($mode -eq 'isolated-existing-profile') {
            $hasher=[Security.Cryptography.SHA256]::Create()
            try {$identity=([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($app.ToLowerInvariant())))).Replace('-','').Substring(0,12)} finally {$hasher.Dispose()}
            $environment.isolated_localappdata=Join-Path (Join-Path $env:LOCALAPPDATA 'SeedanceQA') $identity
        }
        $environment.strict_clean_evidence_eligible=(-not $environment.studio_profile_present -and -not ($environment.preexisting_tools.Values -contains $true))
        $environment | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $prior -Encoding UTF8
        $first=Get-Content -LiteralPath $prior -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    if($mode -eq 'isolated-existing-profile') {
        $isolated=[string]$first.isolated_localappdata
        if(-not $isolated -or $isolated.Length -gt 110){throw '验收缓存路径过长，已停止以避免运行库路径失败；请回传结果。'}
        New-Item -ItemType Directory -Force -Path $isolated | Out-Null
        $env:LOCALAPPDATA=$isolated
        Write-Host '检测到原有Studio目录：保留原配置，改用本验收包专属配置/缓存目录。此结果不会计作全新系统证据。' -ForegroundColor Yellow
    }
    return $first
}
function Get-OwnHealth {
    $marker=Join-Path $app '.runtime_port'
    if(-not (Test-Path -LiteralPath $marker)){return $null}
    $port=(Get-Content -LiteralPath $marker -Raw).Trim()
    if($port -notmatch '^\d{1,5}$'){return $null}
    try {
        $health=Invoke-RestMethod -Uri ('http://127.0.0.1:'+$port+'/api/bootstrap-health') -TimeoutSec 3
        if(-not $health.ok){return $null}
        if([IO.Path]::GetFullPath($health.data.root_dir).TrimEnd('\') -ne [IO.Path]::GetFullPath($app).TrimEnd('\')){throw 'A different installation owns this port'}
        return @{port=$port;data=$health.data}
    } catch {return $null}
}
function Export-Diagnostics {
    # Explicit allowlist: never include .env, profiles, databases, chats, uploads or outputs.
    $collect=Join-Path $diag ('return-'+[Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $collect | Out-Null
    $paths=@()
    $paths+=@(Get-ChildItem -LiteralPath $diag -File -ErrorAction SilentlyContinue | Where-Object {$_.Name -match '\.(json|log)$'})
    $logs=Join-Path $app 'install_logs'
    if(Test-Path -LiteralPath $logs){$paths+=@(Get-ChildItem -LiteralPath $logs -File | Where-Object {$_.Name -match '\.(json|log)$'})}
    foreach($name in @('startup.log','server_direct.log','bootstrap_state.json')) {
        $file=Join-Path (Join-Path $app 'data') $name
        if(Test-Path -LiteralPath $file){$paths+=Get-Item -LiteralPath $file}
    }
    $i=0
    foreach($file in $paths) {
        $i++
        $text=(Get-Content -LiteralPath $file.FullName -Encoding UTF8 -Tail 2000 -ErrorAction SilentlyContinue) -join "`r`n"
        if($text.Length -gt 2000000){$text=$text.Substring($text.Length-2000000)}
        Protect-DiagnosticText $text | Set-Content -LiteralPath (Join-Path $collect ($i.ToString('000')+'-'+$file.Name)) -Encoding UTF8
    }
    $zip=Join-Path $packet '回传结果.zip'
    Compress-Archive -LiteralPath @((Get-ChildItem -LiteralPath $collect -File).FullName) -DestinationPath $zip -Force
    Write-Host ('回传文件：'+$zip) -ForegroundColor Cyan
}
New-Item -ItemType Directory -Force -Path $diag | Out-Null
$lock=$null;$transcript=$false;$failed=$false
$summary=@{phase=$Phase;automated_passed=$false;release_passed=$false;paid_calls=0;manual_checks_pending=$true}
try {
    $lock=[IO.File]::Open((Join-Path $packet '.acceptance.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    Start-Transcript -Path (Join-Path $diag ('acceptance-'+$Phase+'.log')) -Force | Out-Null
    $transcript=$true
    $environment=Get-EnvironmentReport
    $environment | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $diag ('environment-'+$Phase+'.json')) -Encoding UTF8
    Write-Host ('检测到：'+$environment.windows+' '+$environment.display_version+' / '+$environment.architecture)
    if($app.Length -gt 160){throw '解压目录过深，请将完整验收包移到较短目录，例如D:\SeedanceQA，再运行01。当前未开始安装。'}
    if($environment.architecture -ne 'AMD64' -or $environment.build -lt 19041){throw '此验收候选包只测试 Windows 10 2004及以上 / Windows 11 x64；其他系统停止安装，不表示已验证兼容。'}
    Write-Host '正在核对完整安装文件，防止损坏包进入安装……'
    Assert-Payload
    if($Phase -eq 'CheckPacket') {$summary.automated_passed=$true;Write-Host '包完整性和系统检测通过；未安装或启动。'} else {
        $first=Select-AcceptanceContext $environment
        $summary.profile_mode=$first.profile_mode
        $summary.strict_clean_evidence_eligible=$first.strict_clean_evidence_eligible
        if($Phase -eq 'Install') {
            $firstClean=$first.strict_clean_evidence_eligible
            $summary.no_developer_tools_detected=$firstClean
            if(-not $firstClean){Write-Host '注意：检测到已有开发工具；会记录，本次不能独立证明无开发环境安装通过。' -ForegroundColor Yellow}
        } else {
            if(-not $first.boot_time -or -not $environment.boot_time -or $first.boot_time -eq $environment.boot_time){throw '尚未确认系统重启。请保存工作、自行重启Windows，再运行02入口。'}
            $summary.restart_verified=$true
        }
        $ps=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        if($Phase -eq 'Install') {
            & $ps -NoProfile -ExecutionPolicy Bypass -File (Join-Path $app 'tools/install_new_pc.ps1')
            if($LASTEXITCODE -ne 0){throw '安装未通过，请回传结果；不要反复重装或删除日志。'}
            $summary.install=$true
        }
        $python=Join-Path $app '.venv/Scripts/python.exe'
        if(-not (Test-Path -LiteralPath $python)){throw '未找到验收目录的Python环境。'}
        & $env:ComSpec /d /c ('"'+(Join-Path $app '00_START.cmd')+'"')
        if($LASTEXITCODE -ne 0){throw '正式启动入口失败。'}
        $deadline=[DateTime]::UtcNow.AddMinutes(3)
        $health=$null
        while([DateTime]::UtcNow -lt $deadline){$health=Get-OwnHealth;if($health){break};Start-Sleep -Seconds 1}
        if(-not $health){throw '正式入口启动后未能确认对应目录服务就绪。'}
        $summary.formal_launcher=$true
        & $python (Join-Path $PSScriptRoot 'free_smoke.py') --root $app --port $health.port --report (Join-Path $diag ('free-runtime-'+$Phase+'.json'))
        if($LASTEXITCODE -ne 0){throw '免费官方合成/Studio验收未通过。'}
        $summary.free_runtime=$true
        & $ps -NoProfile -ExecutionPolicy Bypass -File (Join-Path $app 'tools/install_new_pc.ps1') -CheckOnly
        if($LASTEXITCODE -ne 0){throw '启动后运行组件复核失败。'}
        $summary.runtime_recheck=$true
        $summary.automated_passed=$true
        Write-Host '自动检查通过。请按说明进行页面检查，之后自行重启并运行02入口。不要填写服务密钥或点击付费生成。' -ForegroundColor Green
    }
} catch {
    $failed=$true;$summary.error=$_.Exception.Message
    Write-Host ('验收停止：'+$_.Exception.Message) -ForegroundColor Red
} finally {
    if($lock) {
        $summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $diag ('summary-'+$Phase+'.json')) -Encoding UTF8
        if($transcript){Stop-Transcript | Out-Null}
        try {Export-Diagnostics} catch {Write-Host ('自动压缩失败，请回传“验收结果”文件夹内的json/log：'+$_.Exception.Message)}
        $lock.Dispose()
    }
}
if($failed){exit 1}
