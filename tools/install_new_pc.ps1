#requires -Version 5.1
<#
Seedance Studio 5.2.0 - Windows fresh PC installer.
Only obtains Python and public dependencies; never touches saved Studio data or API keys.
Python registration is resolved using PEP 514, launcher, and validated executable paths.
#>
param([switch]$CheckOnly, [switch]$DiagnoseOnly)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'Continue'
# Windows PowerShell 5.1 must explicitly decode UTF-8 output from Python and pip.
# chcp 65001 in CMD alone does NOT fix native output decoding in PowerShell.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
try { [Console]::OutputEncoding = $utf8NoBom; [Console]::InputEncoding = $utf8NoBom } catch {}
$OutputEncoding = $utf8NoBom
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:PIP_PROGRESS_BAR = 'on'

$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$pyVersion='3.12.10'
$arch='amd64'
if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { $arch='arm64' }
$pyName="python-$pyVersion-$arch.exe"
$officialUrl="https://www.python.org/ftp/python/$pyVersion/$pyName"
$perUserTarget=Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312'
$logDir=Join-Path $root 'install_logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$diagnosticLog=Join-Path $logDir 'python_setup_v426.log'

# Colored, stage-based installation progress. Percent reflects completed steps
# only; actual network transfer displays true byte progress separately.
function Draw-Phase([int]$completed, [string]$headline) {
    $total=5
    $done=[Math]::Min($total,[Math]::Max(0,$completed))
    $count=$done*6
    $bar=('=' * $count)+('-' * (30-$count))
    $pct=[int][Math]::Round(($done*100.0)/$total)
    Write-Host ''
    Write-Host ('  ['+$bar+'] '+$pct+'%  '+$headline) -ForegroundColor Cyan
    Write-Progress -Activity 'Seedance Studio - Windows setup' -Status $headline -PercentComplete $pct
}
function Draw-Download([long]$bytes, [long]$total) {
    $mb=[Math]::Round($bytes / 1MB,1)
    if($total -gt 0) {
        $percent=[int][Math]::Min(100,[Math]::Round($bytes*100.0/$total))
        $full=[int][Math]::Floor($percent/5)
        $bar=('='*$full)+('-'*(20-$full))
        Write-Host ("`r  Python download ["+$bar+"] "+$percent+'%  '+$mb+' MB') -NoNewline -ForegroundColor Yellow
        Write-Progress -Activity 'Downloading official Python installer' -Status ($mb.ToString()+' MB') -PercentComplete $percent
    } else {Write-Host ("`r  Python download: "+$mb+' MB received') -NoNewline -ForegroundColor Yellow}
}
function Download-OfficialPython([string]$url, [string]$output) {
    # Stream bytes rather than Invoke-WebRequest, which hides transfer progress
    # in Windows PowerShell 5.1 and can appear frozen on slow networks.
    Add-Type -AssemblyName System.Net.Http
    $client=New-Object System.Net.Http.HttpClient
    $client.Timeout=[TimeSpan]::FromMinutes(8)
    $response=$null;$inputStream=$null;$outputStream=$null
    try {
        $response=$client.GetAsync($url,[System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        $response.EnsureSuccessStatusCode() | Out-Null
        $total=[long]($response.Content.Headers.ContentLength)
        $inputStream=$response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $outputStream=[System.IO.File]::Open($output,[System.IO.FileMode]::Create,[System.IO.FileAccess]::Write,[System.IO.FileShare]::None)
        $buffer=New-Object byte[] 262144
        $received=[long]0;$last=[datetime]::UtcNow.AddSeconds(-5)
        while(($n=$inputStream.Read($buffer,0,$buffer.Length)) -gt 0) {
            $outputStream.Write($buffer,0,$n)
            $received+=$n
            if(([datetime]::UtcNow-$last).TotalMilliseconds -gt 600) {
                Draw-Download $received $total
                $last=[datetime]::UtcNow
            }
        }
        Draw-Download $received $total
        Write-Host ''
        Write-Progress -Activity 'Downloading official Python installer' -Completed
        if($total -gt 0 -and $received -ne $total) { throw ('Partial download: '+$received+'/'+$total) }
        if($received -lt 20000000) {throw 'Python download is unexpectedly small; retry installation.'}
    } finally {
        if($outputStream){$outputStream.Dispose()}
        if($inputStream){$inputStream.Dispose()}
        if($response){$response.Dispose()}
        $client.Dispose()
    }
}
function Log-Line([string]$msg) {
    $line="[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Add-Content -LiteralPath $diagnosticLog -Value $line -Encoding UTF8
    Write-Host $msg
}
function Test-Python([string]$exe, [string[]]$prefix=@()) {
    if (-not $exe) { return $false }
    # Windows PowerShell 5.1 treats native stderr as an error when
    # $ErrorActionPreference='Stop'.  Do not use `& python ... 2>&1`
    # here: it can mark an otherwise working Python installation as broken.
    # Use ProcessStartInfo and check exit code, stdout and architecture.
    try {
        $psi=New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName=$exe
        $pyCode="import sys,struct;print(sys.version_info.major,sys.version_info.minor,struct.calcsize('P')*8)"
        $psi.Arguments=((@($prefix) -join ' ') + ' -c "' + $pyCode + '"').Trim()
        $psi.UseShellExecute=$false
        $psi.RedirectStandardOutput=$true
        $psi.RedirectStandardError=$true
        $psi.CreateNoWindow=$true
        $proc=New-Object System.Diagnostics.Process
        $proc.StartInfo=$psi
        if (-not $proc.Start()) { throw 'Process.Start returned false' }
        $stdout=$proc.StandardOutput.ReadToEnd().Trim()
        $stderr=$proc.StandardError.ReadToEnd().Trim()
        if (-not $proc.WaitForExit(12000)) {
            try {$proc.Kill()} catch {}
            Log-Line ('Python check TIMEOUT: '+$exe)
            return $false
        }
        $m=[regex]::Match($stdout,'(?m)^3\s+(10|11|12)\s+64\s*$')
        if ($proc.ExitCode -eq 0 -and $m.Success) {return $true}
        $details=('exit='+$proc.ExitCode+' stdout='+$stdout+' stderr='+$stderr)
        if ($details.Length -gt 650) {$details=$details.Substring(0,650)}
        Log-Line ('Python candidate check failed: '+$exe+' '+$details)
        return $false
    } catch {
        Log-Line ('Python cannot start: '+$exe+' : '+$_.Exception.Message)
        return $false
    }
}

function Registered-PythonPaths {
    # PEP 514: HKCU/HKLM Software/Python/PythonCore/<version>/InstallPath.
    # Registry is often the ONLY correct way to discover the official install path.
    $bases=@(
        'Registry::HKEY_CURRENT_USER\Software\Python\PythonCore',
        'Registry::HKEY_LOCAL_MACHINE\Software\Python\PythonCore',
        'Registry::HKEY_LOCAL_MACHINE\Software\WOW6432Node\Python\PythonCore'
    )
    foreach ($base in $bases) {
        if (-not(Test-Path -LiteralPath $base)) { continue }
        foreach($version in @(Get-ChildItem -LiteralPath $base -ErrorAction SilentlyContinue)) {
            $installKey = Join-Path $version.PSPath 'InstallPath'
            if (-not(Test-Path -LiteralPath $installKey)) { continue }
            try {
                $key=Get-Item -LiteralPath $installKey -ErrorAction Stop
                $folder=[string]$key.GetValue('')
                $exe=[string]$key.GetValue('ExecutablePath')
                if ($exe) {$exe}
                if ($folder) {Join-Path $folder 'python.exe'}
            } catch { continue }
        }
    }
}
function Candidate-PythonPaths {
    # Query installed interpreters without scanning an entire drive.
    $paths=@(
      (Join-Path $perUserTarget 'python.exe'),
      (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312-64\python.exe'),
      (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'),
      (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python310\python.exe'),
      (Join-Path $env:ProgramFiles 'Python312\python.exe'),
      (Join-Path $env:ProgramFiles 'Python311\python.exe'),
      (Join-Path $env:ProgramFiles 'Python310\python.exe')
    )
    if ($env:ProgramW6432) { $paths += (Join-Path $env:ProgramW6432 'Python312\python.exe') }
    $paths+=@(Registered-PythonPaths)
    # Python may register under Python312-64, Python312-32, or another version folder.
    foreach($folder in @(Join-Path $env:LOCALAPPDATA 'Programs\Python'),$env:ProgramFiles) {
        if (-not(Test-Path -LiteralPath $folder)) {continue}
        $found=@(Get-ChildItem -LiteralPath $folder -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^(Python3(10|11|12))(-64)?$' })
        foreach($d in $found) {$paths+=(Join-Path $d.FullName 'python.exe')}
    }
    if ($env:CONDA_PREFIX) {$paths += (Join-Path $env:CONDA_PREFIX 'python.exe')}
    if ($env:VIRTUAL_ENV) {$paths += (Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe')}
    return @($paths | Where-Object {$_} | Select-Object -Unique)
}
function Detect-Python {
    foreach($candidate in @(Candidate-PythonPaths)) {
        if ((Test-Path -LiteralPath $candidate -PathType Leaf) -and (Test-Python -exe $candidate)) {
            return [PSCustomObject]@{Exe=$candidate;Args=@()}
        }
    }
    $cmd=Get-Command py.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        foreach($ver in @('-3.12','-3.11','-3.10')) {
            if (Test-Python -exe $cmd.Source -prefix @($ver)) {
                return [PSCustomObject]@{Exe=$cmd.Source;Args=@($ver)}
            }
        }
    }
    $cmd=Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch '\\WindowsApps\\' -and (Test-Python -exe $cmd.Source)) {
        return [PSCustomObject]@{Exe=$cmd.Source;Args=@()}
    }
    return $null
}
function Validate-PythonInstaller([string]$file) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw 'Python installer file not found.' }
    if ((Get-Item -LiteralPath $file).Length -lt 20000000) { throw 'Python installer file is too small.' }
    $sig=Get-AuthenticodeSignature -LiteralPath $file
    if ($sig.Status -ne 'Valid' -or $null -eq $sig.SignerCertificate -or
        $sig.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        throw ('Official Python installer signature check failed. Status='+[string]$sig.Status)
    }
    Log-Line 'Official Python installer Authenticode signature: VALID.'
}
function Get-PythonInstaller {
    $manual=Join-Path (Join-Path $root 'installers') $pyName
    if (Test-Path -LiteralPath $manual -PathType Leaf) { Validate-PythonInstaller $manual;return $manual }
    $cacheDir=Join-Path $env:LOCALAPPDATA 'SeedanceStudio\downloads'
    New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
    $cache=Join-Path $cacheDir $pyName
    if (Test-Path -LiteralPath $cache) {
        try {Validate-PythonInstaller $cache;return $cache}
        catch {Log-Line 'Cached installer invalid: redownloading.'}
    }
    $temp=Join-Path $cacheDir ($pyName+'.partial')
    if (Test-Path -LiteralPath $temp) {Remove-Item -LiteralPath $temp -Force}
    Log-Line 'Downloading Python 3.12.10 from official python.org (no winget)...'
    try {
        [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
        Download-OfficialPython $officialUrl $temp
        Validate-PythonInstaller $temp
        Move-Item -LiteralPath $temp -Destination $cache -Force
        return $cache
    } catch {
        if (Test-Path -LiteralPath $temp) {Remove-Item -LiteralPath $temp -Force}
        throw ('Python download failed: '+$_.Exception.Message+'. Manual download: '+$officialUrl)
    }
}
function Install-Python {
    $installer=Get-PythonInstaller
    $installerLog=Join-Path $logDir 'python312_official_installer.log'
    Log-Line ('Starting signed official Python 3.12.10 installer. Current Windows user: '+[Security.Principal.WindowsIdentity]::GetCurrent().Name)
    Log-Line ('Requested target directory: '+$perUserTarget)
    # Required components are explicitly selected, and installer writes a log.
    $arguments='/quiet InstallAllUsers=0 Include_exe=1 Include_lib=1 Include_dev=1 Include_pip=1 Include_launcher=0 Include_test=0 PrependPath=0 TargetDir="'+$perUserTarget+'" /log "'+$installerLog+'"'
    $proc=Start-Process -FilePath $installer -ArgumentList $arguments -PassThru -Wait
    Log-Line ('Official Python installer process exit code: '+$proc.ExitCode)
    if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {
        throw ('Python setup returned '+$proc.ExitCode+'. See install_logs/python312_official_installer.log for details.')
    }
    # Re-read registry and check actual executable, not just the installer status.
    for ($i=1;$i -le 6;$i++) {
        $found=Detect-Python
        if ($found) { return $found }
        if ($i -lt 6) { Start-Sleep -Seconds 2 }
    }
    if ($proc.ExitCode -eq 3010) {
        throw 'Windows reported a reboot is required. Restart Windows, then rerun the same installer. Do NOT reinstall Python manually first.'
    }
    Log-Line 'Official installer exited 0, but none of the discovered executables passed 64-bit Python version checks.'
    Log-Line 'No repeated installation attempts. The log above shows exactly why Python is not runnable.'
    throw ('Python installer exited successfully but Python is not yet runnable. Open install_logs/python_setup_v426.log and python312_official_installer.log. Run 01_INSTALL.cmd again after restarting Windows. See the specific Python error in python_setup_v426.log.')
}
function Show-PythonCandidates {
    Log-Line 'Python locations discovered on this computer (paths only):'
    $candidates=@(Candidate-PythonPaths)
    if (-not $candidates.Count) {Log-Line 'No Python candidates from registry or standard folders.'}
    foreach ($candidate in $candidates) {
        $found=Test-Path -LiteralPath $candidate -PathType Leaf
        Log-Line ("Exists=$found | $candidate")
    }
    $cmd=Get-Command py.exe -ErrorAction SilentlyContinue
    if ($cmd) {Log-Line ('Python Launcher: '+$cmd.Source)} else {Log-Line 'Python Launcher: not found'}
}
try {
    Draw-Phase 0 'Checking compatible Python and Windows environment'
    Log-Line '============================================================='
    Log-Line 'Seedance Studio V5.0.0 | Windows new PC setup + PEP514 detection'
    Log-Line 'Single installer entry: 01_INSTALL.cmd. Existing Python is reused when verified.'
    $python=Detect-Python
    if (-not $python) {
        Show-PythonCandidates
        if ($CheckOnly -or $DiagnoseOnly) {Log-Line 'No compatible Python found.';exit 2}
        $python=Install-Python
    } else { Log-Line 'Compatible Python found; skipping download and setup.' }
    Draw-Phase 1 'Python verified (reuse or signed Python.org install)'
    Log-Line ('Compatible Python interpreter: '+$python.Exe+' '+($python.Args -join ' '))
    $exe=[string]$python.Exe;$arguments=@($python.Args)
    if (-not(Test-Python -exe $exe -prefix $arguments)) { throw 'Final interpreter self-check failed.' }
    if (-not $CheckOnly -and -not $DiagnoseOnly) {
        & $exe @arguments (Join-Path $root 'tools\install_hypit_bundle.py')
        if ($LASTEXITCODE -ne 0) { throw 'Offline Hypit runtime extraction failed; existing runtime and data were preserved.' }
    }
    Log-Line 'Checking bundled official Hypit, Node and browser (offline, no model calls).'
    & $exe @arguments (Join-Path $root 'tools\check_hypit_install.py')
    if ($LASTEXITCODE -ne 0) { throw 'Hypit runtime verification failed. Re-extract the complete Windows x64 installation bundle.' }
    if ($CheckOnly -or $DiagnoseOnly) {Log-Line 'Python installation verified.';exit 0}
    Draw-Phase 2 'Preparing video and audio dependencies (detailed stages below)'
    Log-Line 'Python dependencies and video tools: inner 7 verification steps. Every completed check is logged.'
    & $exe @arguments (Join-Path $root 'tools\bootstrap_install.py')
    if ($LASTEXITCODE -ne 0) { throw ('Dependency installation incomplete (exit '+$LASTEXITCODE+'). Review install_logs/dependency_report.json and rerun 01_INSTALL.cmd.') }
    Draw-Phase 3 'Installer returned; validating actual installed files'
    $venv=Join-Path $root '.venv\Scripts\python.exe'
    if (-not(Test-Path -LiteralPath $venv -PathType Leaf)) { throw 'Studio virtual environment not found after bootstrap.' }
    & $venv (Join-Path $root 'tools\check_timezone.py')
    if ($LASTEXITCODE -ne 0) { throw 'Timezone self-check failed. Verify assets/timezones/America_New_York.tzif.' }
    Draw-Phase 4 'Validating health report and required components'
    $report = Join-Path $logDir 'dependency_report.json'
    if (Test-Path -LiteralPath $report) {
        $summary = Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json
        Log-Line ('Dependency status: '+$summary.status)
        if ($summary.required_missing.Count -gt 0) {
            throw ('Required dependencies missing: '+($summary.required_missing -join ', '))
        }
        if ($summary.enhanced_missing.Count -gt 0) {
            Log-Line ('WARNING: Advanced components unavailable: '+($summary.enhanced_missing -join ', '))
            Log-Line 'This is not a full advanced-capability pass. Re-run 01 after checking the logs / compatibility.'
        }
    } else {throw 'Dependency report not generated. Re-run 01_INSTALL.cmd.'}
    Draw-Phase 5 'Complete - installation verified'
    Write-Progress -Activity 'Seedance Studio - Windows setup' -Completed
    Log-Line 'SETUP PASSED. Double-click 00_START.cmd to open the Studio. Advanced warnings are in dependency_report.json.'
    exit 0
} catch {
    Write-Progress -Activity 'Seedance Studio - Windows setup' -Completed
    Write-Host ('  INSTALL FAILED: '+$_.Exception.Message) -ForegroundColor Red
    Log-Line ('SETUP FAILED: '+$_.Exception.Message)
    Log-Line ('Diagnostic log: '+$diagnosticLog)
    Log-Line 'Your data, API settings and previous installation were NOT erased.'
    exit 3
}
