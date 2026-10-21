[CmdletBinding()]
param([switch]$Resume,[string]$InstallDir,[string]$EvidenceRoot,[string]$PackageRoot)
$ErrorActionPreference='Stop'
$utf8=New-Object Text.UTF8Encoding $false
$package=if ($PackageRoot) {[IO.Path]::GetFullPath($PackageRoot)} else {Split-Path -Parent $MyInvocation.MyCommand.Path}
$manifest=Get-Content -LiteralPath (Join-Path $package 'BUILD_MANIFEST.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$version=$manifest.version.APP_VERSION+'-'+$manifest.version.APP_BUILD
$qaRoot=Join-Path $env:LOCALAPPDATA 'SeedanceStudioAcceptance'
New-Item -ItemType Directory -Path $qaRoot -Force | Out-Null
$marker=Join-Path $qaRoot ('Last-'+$manifest.installer.sha256.Substring(0,16)+'.json')
$report=[ordered]@{format='seedance.newpc-acceptance@1';started_utc=[DateTime]::UtcNow.ToString('o');paid_calls=0;checks=@();status='RUNNING';fresh_os_claim='Requires operator confirmation; absence of old files alone is not proof';os_reboot='NOT TESTED';production_updates='BLOCKED';real_paid_services='NOT TESTED'}
$process=$null;$token=$null;$url=$null;$stop=$null
function Save-Json($path,$value) { [IO.File]::WriteAllText($path,($value | ConvertTo-Json -Depth 50),$utf8) }
function Assert($condition,$message) { if (!$condition) { throw $message } }
function Check($name,$body) {
    Write-Host ('CHECK: '+$name)
    $watch=[Diagnostics.Stopwatch]::StartNew()
    & $body
    $report.checks+=@{name=$name;status='PASS';seconds=[Math]::Round($watch.Elapsed.TotalSeconds,2)}
}
function Json-Request($route,$body=$null) {
    Assert ($route.StartsWith('/') -and !$route.StartsWith('//')) 'Local API route required'
    $request=[Net.HttpWebRequest]::Create($script:url+$route)
    $request.Proxy=$null;$request.Timeout=180000;$request.ReadWriteTimeout=180000
    if ($script:token) { $request.Headers.Add('X-Hypit-Session',$script:token) }
    if ($null -ne $body) {
        $request.Method='POST';$request.ContentType='application/json'
        $bytes=$utf8.GetBytes(($body | ConvertTo-Json -Depth 40 -Compress))
        $request.ContentLength=$bytes.Length
        $stream=$request.GetRequestStream();try { $stream.Write($bytes,0,$bytes.Length) } finally { $stream.Dispose() }
    }
    $response=$request.GetResponse()
    try { $reader=New-Object IO.StreamReader($response.GetResponseStream(),$utf8);try {$value=$reader.ReadToEnd() | ConvertFrom-Json} finally {$reader.Dispose()} } finally {$response.Dispose()}
    Assert ($value.ok -eq $true) ('API refused '+$route+': '+$value.error)
    return $value.data
}
function Start-App($name,[switch]$Gui) {
    $script:ready=Join-Path $script:evidence ($name+'-ready.json')
    $script:stop=Join-Path $script:evidence ($name+'-stop')
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=$script:exe;$info.UseShellExecute=$false;$info.CreateNoWindow=$true
    $arguments=@('--data-home',('"'+$script:testHome+'"'),'--ready-file',('"'+$script:ready+'"'),'--shutdown-file',('"'+$script:stop+'"'),'--skip-migration-prompt')
    if ($Gui) { $arguments+=@('--gui-evidence',('"'+(Join-Path $script:evidence 'gui')+'"'),'--gui-smoke-seconds','60') } else { $arguments+='--headless' }
    $info.Arguments=$arguments -join ' '
    foreach ($name in @($info.EnvironmentVariables.Keys)) {
        if ($name -match 'KEY|TOKEN|SECRET|PASSWORD|^STUDIO_|^PYTHON|^OPENLUX_|^BRIGHTDATA_|^OSS_') { $info.EnvironmentVariables.Remove($name) }
    }
    $script:process=[Diagnostics.Process]::Start($info)
    $end=[DateTime]::UtcNow.AddSeconds(190)
    while ([DateTime]::UtcNow -lt $end) {
        if ($script:process.HasExited) { throw ('Actual EXE exited before readiness: '+$script:process.ExitCode) }
        if (Test-Path -LiteralPath $script:ready) {
            $state=Get-Content -LiteralPath $script:ready -Raw -Encoding UTF8 | ConvertFrom-Json
            if (!$Gui -or $state.gui -eq 'edgechromium-loaded') {
                Assert ($state.url -match '^http://127\.0\.0\.1:[0-9]+$') 'Unexpected readiness URL'
                $script:url=$state.url
                $health=Json-Request '/api/bootstrap-health'
                Assert ($health.pid -eq $state.pid -and $health.version -eq $manifest.version.APP_VERSION -and $health.build -eq $manifest.version.APP_BUILD) 'Health process/version identity mismatch'
                Assert ([IO.Path]::GetFullPath($health.data_dir) -eq [IO.Path]::GetFullPath((Join-Path $script:testHome 'data'))) 'Test app selected another user data directory'
                $script:token=(Json-Request '/api/hypit/session').token
                return
            }
        }
        Start-Sleep -Milliseconds 300
    }
    throw 'Bounded EXE startup timeout; test data and logs preserved'
}
function Stop-App {
    if ($script:process -and !$script:process.HasExited) {
        [IO.File]::WriteAllText($script:stop,'stop')
        if (!$script:process.WaitForExit(40000)) { $script:process.Kill();throw 'Graceful shutdown failed; only this owned process was stopped' }
        Assert ($script:process.ExitCode -eq 0) ('Actual EXE shutdown code '+$script:process.ExitCode)
    }
    $script:process=$null;$script:token=$null
}
try {
    try {$os=Get-CimInstance Win32_OperatingSystem;$report.os=@{caption=$os.Caption;version=$os.Version;build=$os.BuildNumber;architecture=$os.OSArchitecture};$boot=$os.LastBootUpTime.ToUniversalTime().ToString('o')} catch {$report.os=@{version=[Environment]::OSVersion.Version.ToString()};$boot=$null}
    if ($Resume) {
        Assert (Test-Path -LiteralPath $marker) 'No first-stage acceptance state; run 01 first'
        $previous=Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
        Assert ($previous.installer_sha256 -eq $manifest.installer.sha256) 'Resume belongs to another installer'
        Assert ([IO.Path]::GetFullPath($previous.home) -eq [IO.Path]::GetFullPath((Join-Path $previous.evidence 'Test User Data'))) 'Resume test data path is not the original isolated home'
        $script:evidence=Join-Path $previous.evidence ('resume-'+[Guid]::NewGuid().ToString('N').Substring(0,8))
        New-Item -ItemType Directory -Path $script:evidence | Out-Null
        $script:testHome=$previous.home;$script:exe=$previous.exe
        Check 'actual OS reboot evidence' { Assert ($boot -and $previous.boot_utc -and [DateTime]$boot -gt [DateTime]$previous.boot_utc) 'No verified OS reboot since stage 1; do not mark reboot passed';$report.os_reboot='PASS' }
    } else {
        if (!$EvidenceRoot) {$EvidenceRoot=Join-Path $qaRoot ('Run-'+[DateTime]::Now.ToString('yyyyMMdd-HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,6))}
        Assert (!(Test-Path -LiteralPath $EvidenceRoot)) 'Evidence directory already exists; nothing overwritten'
        $script:evidence=[IO.Path]::GetFullPath($EvidenceRoot);New-Item -ItemType Directory -Path $script:evidence | Out-Null
        $script:testHome=Join-Path $script:evidence 'Test User Data'
        $setup=Join-Path $package ([IO.Path]::GetFileName($manifest.installer.path))
        Check 'installer SHA256 before execution' { Assert ((Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLower() -eq $manifest.installer.sha256) 'Installer SHA mismatch; no installer executed' }
        $key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8F130024-CF24-4E11-8185-D460C6B2E7B8}_is1'
        if (Test-Path -LiteralPath $key) {
            $registered=(Get-ItemProperty -LiteralPath $key).InstallLocation
            if ($InstallDir) {Assert ([IO.Path]::GetFullPath($InstallDir).TrimEnd('\') -eq [IO.Path]::GetFullPath($registered).TrimEnd('\')) 'Another registered install was preserved; explicit QA path does not match it'}
            $InstallDir=$registered
            $report.installation='Native upgrade/reinstall into registered product root; not a first install'
        } else {
            if (!$InstallDir) {$InstallDir=Join-Path $env:LOCALAPPDATA 'Programs\SeedanceStudio'}
            Assert (!(Test-Path -LiteralPath $InstallDir) -or @(Get-ChildItem -LiteralPath $InstallDir -Force).Count -eq 0) 'Unregistered nonempty install directory preserved; choose a new path'
            $report.installation='Native EXE first product install on this OS; operator must attest whether OS was clean'
        }
        Check 'complete native installer (or safe registered upgrade/repair)' {
                $arguments='/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /MERGETASKS=!desktopicon /DIR="'+$InstallDir+'" /LOG="'+(Join-Path $script:evidence 'install.log')+'"'
                $install=Start-Process -FilePath $setup -ArgumentList $arguments -WindowStyle Hidden -PassThru
                $end=[DateTime]::UtcNow.AddMinutes(15)
                while (!$install.HasExited -and [DateTime]::UtcNow -lt $end) {Write-Progress -Activity 'Installing complete Seedance Studio' -Status 'Copying and verifying bundled runtimes; actual installer log is being saved';Start-Sleep -Seconds 1;$install.Refresh()}
                Assert ($install.HasExited) ('Installer exceeded deadline; process '+$install.Id+' preserved. Do not run another installer until it finishes.')
                Assert ($install.ExitCode -eq 0) ('Installer failed with exit '+$install.ExitCode+'; review install.log')
        }
        $script:exe=Join-Path $InstallDir ('versions\'+$version+'\SeedanceStudio.exe')
    }
    $payload=Split-Path -Parent $script:exe
    Check 'complete installed payload identity' {
        $index=0
        foreach ($file in $manifest.payload_files) {
            $path=Join-Path $payload $file.path
            Assert (Test-Path -LiteralPath $path -PathType Leaf) ('Missing installed file: '+$file.path)
            Assert ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower() -eq $file.sha256) ('Installed file SHA mismatch: '+$file.path)
            $index++;if ($index%25 -eq 0) {Write-Progress -Activity 'Verifying actual installed files' -Status ($index.ToString()+' / '+$manifest.payload_files.Count) -PercentComplete (100*$index/$manifest.payload_files.Count)}
        }
    }
    Check 'actual backend startup with bundled runtimes' {Start-App 'first'}
    if ($manifest.component_distribution -eq 'background-pinned') {
        Check 'base UI ready without optional plugin downloads' {
            $components=Json-Request '/api/components/status'
            Assert (!$components.active) 'Fresh profile unexpectedly downloaded a plugin before consent'
        }
        Check 'explicitly authorized pinned background components and verification' {
            $initial=Json-Request '/api/components/status'
            foreach ($component in $initial.components) {
                Json-Request '/api/components/download' @{id=$component.id;confirmed=$true} | Out-Null
                $deadline=[DateTime]::UtcNow.AddMinutes(30)
                do {
                $components=Json-Request '/api/components/status'
                Save-Json (Join-Path $script:evidence 'components.json') $components
                $failed=@($components.components | Where-Object {$_.id -eq $component.id -and $_.state -in @('failed','install_failed','needs_repair','paused')})
                Assert (!$failed.Count) ('Component provisioning stopped: '+(($failed | ForEach-Object {$_.title+': '+$_.error}) -join '; '))
                $remaining=@($components.components | Where-Object {$_.id -eq $component.id -and $_.state -ne 'ready'})
                if (!$remaining.Count) {break}
                Write-Progress -Activity 'Downloading pinned components from reviewed domestic / official sources' -Status (($remaining | ForEach-Object {$_.title+': '+[Math]::Round($_.downloaded/1MB,1)+' / '+[Math]::Round($_.total/1MB,1)+' MB'}) -join '; ')
                Start-Sleep -Seconds 2
                } while ([DateTime]::UtcNow -lt $deadline)
                Assert (!$remaining.Count) 'Background component provisioning exceeded bounded deadline; resumable files retained'
            }
        }
    }
    Check 'official Hypit and FFmpeg availability' {$health=Json-Request '/api/hypit/health';Assert ($health.available -and $health.local_build -and $health.skill_installed) 'Official Hypit/FFmpeg/Skill unavailable'}
    Check 'configuration persistence without provider calls' {
        $document=Json-Request '/api/configuration';$revision=$document.revision
        foreach ($provider in $document.providers) {Assert (!$provider.key_configured) 'QA profile unexpectedly contains an account key';$provider.models.chat='offline-acceptance-model'}
        $saved=Json-Request '/api/configuration' @{document=$document;expected_revision=$revision;secret_updates=@{}}
        Assert ($saved.revision -eq $revision+1) 'Configuration revision did not advance'
    }
    Check 'production update remains safely unconfigured' {$state=Json-Request '/api/updates/status';Assert ($state.state -eq 'BLOCKED') 'Unexpected production update trust configuration'}
    Check 'graceful shutdown' {Stop-App}
    Check 'restart and saved model retained' {Start-App 'restart';$document=Json-Request '/api/configuration';foreach ($provider in $document.providers) {Assert ($provider.models.chat -eq 'offline-acceptance-model') 'Saved configuration missing after restart'}}
    Check 'second graceful shutdown' {Stop-App}
    Check 'actual bundled WebView2 GUI loaded' {Start-App 'gui' -Gui;Assert (Test-Path -LiteralPath (Join-Path $script:evidence 'gui\desktop-webview.png')) 'Actual GUI evidence screenshot missing';Stop-App}
    $report.status='PASS tested stages only'
    if (!$Resume) {
        Save-Json $marker @{evidence=$script:evidence;home=$script:testHome;exe=$script:exe;boot_utc=$boot;installer_sha256=$manifest.installer.sha256}
        Write-Host 'Stage 1 finished. Reboot manually, then run 02_RESUME_AFTER_REBOOT.cmd. No automatic reboot is performed.'
    }
} catch {
    $report.error=$_.Exception.Message
    $blocked=@('No verified OS reboot','Another registered install','Unregistered nonempty install',
               'No first-stage acceptance state','Evidence directory already exists')
    $report.status=if (@($blocked | Where-Object {$report.error.StartsWith($_)}).Count) {'BLOCKED'} else {'FAIL'}
    Write-Host ($report.status+': '+$report.error) -ForegroundColor $(if ($report.status -eq 'BLOCKED') {'Yellow'} else {'Red'})
} finally {
    try {Stop-App} catch {$report.cleanup_error=$_.Exception.Message;$report.status='FAIL'}
    if ($script:evidence -and (Test-Path -LiteralPath $script:evidence)) {
        Save-Json (Join-Path $script:evidence 'ACCEPTANCE.json') $report
        $share=Join-Path $script:evidence 'Share';New-Item -ItemType Directory -Path $share -Force | Out-Null
        Get-ChildItem -LiteralPath $script:evidence -File | Where-Object {$_.Extension -in '.json','.log'} | Copy-Item -Destination $share
        if (Test-Path -LiteralPath (Join-Path $script:evidence 'gui')) {Copy-Item -LiteralPath (Join-Path $script:evidence 'gui') -Destination $share -Recurse}
        $zip=$script:evidence+'-Result.zip';Compress-Archive -LiteralPath $share -DestinationPath $zip
        try {Copy-Item -LiteralPath $zip -Destination (Join-Path $package ([IO.Path]::GetFileName($zip)))} catch {Write-Host 'Package folder is read-only; use the result ZIP path below.'}
        Write-Host ('RESULT ZIP: '+$zip)
        Write-Host 'Only isolated test logs and screenshots are exported; no real profile/database/credentials are included.'
    }
}
if ($report.status -like 'FAIL*') {exit 1}
if ($report.status -eq 'BLOCKED') {exit 2}
