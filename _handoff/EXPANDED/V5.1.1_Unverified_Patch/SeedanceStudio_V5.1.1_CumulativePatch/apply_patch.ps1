param([string]$Target = '')
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Stop-Patch([string]$Message) { throw $Message }
function Hash-Of([string]$Path) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function App-Root([string]$Requested, [string]$PatchRoot) {
    if ($Requested) {
        $Resolved = (Resolve-Path -LiteralPath $Requested -ErrorAction Stop).Path
        if (Test-Path -LiteralPath (Join-Path $Resolved 'core/config.py') -PathType Leaf) { return $Resolved }
        Stop-Patch ('The selected folder is not the program root: ' + $Resolved)
    }
    # Allow both extracting the patch folder under the application root and extracting its files directly into the root.
    $Parent = Split-Path -Parent $PatchRoot
    if (Test-Path -LiteralPath (Join-Path $Parent 'core/config.py') -PathType Leaf) { return $Parent }
    if (Test-Path -LiteralPath (Join-Path $PatchRoot 'core/config.py') -PathType Leaf) { return $PatchRoot }
    Stop-Patch 'Program folder not found. Extract this patch INSIDE the folder containing 00_START.cmd, then run 01_APPLY_PATCH.cmd.'
}
# The browser is not the application process: closing a tab does not stop the backend.
# Never mistake a recycled PID or somebody else's port 8765 for this Studio instance.
function Canonical-Root([string]$Path) {
    return [IO.Path]::GetFullPath($Path).TrimEnd([char[]]@([char]92,[char]47)).ToLowerInvariant()
}
function Local-Api([int]$Port, [string]$Path, [string]$Method = 'GET') {
    $Uri = 'http://127.0.0.1:' + $Port + $Path
    $Request = [System.Net.HttpWebRequest][System.Net.WebRequest]::Create($Uri)
    $Request.Proxy = $null  # VPN, corporate proxies, and HTTP_PROXY must not intercept localhost.
    $Request.Method = $Method
    $Request.Timeout = 2500
    $Request.ReadWriteTimeout = 2500
    if ($Method -eq 'POST') { $Request.ContentLength = 0 }
    $Response = $null
    $Reader = $null
    try {
        $Response = [System.Net.HttpWebResponse]$Request.GetResponse()
        $Reader = New-Object System.IO.StreamReader($Response.GetResponseStream(),[System.Text.Encoding]::UTF8)
        return ($Reader.ReadToEnd() | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        return $null
    } finally {
        if ($null -ne $Reader) { $Reader.Close() }
        if ($null -ne $Response) { $Response.Close() }
    }
}
function Studio-On-Port([int]$Port, [string]$Root) {
    if ($Port -lt 1 -or $Port -gt 65535) { return $null }
    $Response = Local-Api $Port '/api/bootstrap-health'
    if ($null -eq $Response -or !$Response.ok -or $null -eq $Response.data) { return $null }
    if (!$Response.data.root_dir) { return $null }
    try {
        if ((Canonical-Root ([string]$Response.data.root_dir)) -ne (Canonical-Root $Root)) { return $null }
    } catch { return $null }
    return [pscustomobject]@{port = $Port; pid = [int]$Response.data.pid; version = [string]$Response.data.version}
}
function Pid-Of-Our-App([int]$ProcessId, [string]$Root) {
    if ($ProcessId -le 0) { return $false }
    $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $Process) { return $false }
    try {
        $Details = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $ProcessId) -ErrorAction Stop
        if ($null -eq $Details -or !$Details.CommandLine) {
            Stop-Patch ('Cannot verify running process PID ' + $ProcessId + '. Please close Studio before updating.')
        }
        $ActualCmd = [string]$Details.CommandLine
        $ExpectedPath = [IO.Path]::Combine($Root,'app.py')
        return ($ActualCmd.IndexOf($ExpectedPath,[StringComparison]::OrdinalIgnoreCase) -ge 0)
    } catch {
        Stop-Patch ('Cannot inspect process PID ' + $ProcessId + '; update stopped to protect active work. ' + $_.Exception.Message)
    }
}
function Candidate-Ports([string]$Root, [int]$ServerPid) {
    $Ports = New-Object 'System.Collections.Generic.List[int]'
    $RuntimePort = Join-Path $Root '.runtime_port'
    if (Test-Path -LiteralPath $RuntimePort -PathType Leaf) {
        $Parsed = 0
        if ([int]::TryParse(((Get-Content -LiteralPath $RuntimePort -Raw).Trim()),[ref]$Parsed)) { $Ports.Add($Parsed) }
    }
    $EnvFile = Join-Path $Root '.env'
    if (Test-Path -LiteralPath $EnvFile -PathType Leaf) {
        foreach ($Line in (Get-Content -LiteralPath $EnvFile -Encoding UTF8 -ErrorAction SilentlyContinue)) {
            if ($Line -match '^\s*PORT\s*=\s*["'']?(\d{2,5})["'']?\s*$') { $Ports.Add([int]$Matches[1]); break }
        }
    }
    $Ports.Add(8765)
    if ($ServerPid -gt 0 -and (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
        foreach ($Connection in @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -eq $ServerPid })) {
            $Ports.Add([int]$Connection.LocalPort)
        }
    }
    return @($Ports | Where-Object { $_ -gt 0 -and $_ -le 65535 } | Select-Object -Unique)
}
function Verify-Stopped([string]$Root) {
    # Detect genuine backend via its root-directory identity, not mere PID/port existence.
    $PidFile = Join-Path $Root 'seedance_studio.pid'
    $ServerPid = 0
    if (Test-Path -LiteralPath $PidFile -PathType Leaf) {
        $Contents = (Get-Content -LiteralPath $PidFile -Raw -ErrorAction Stop).Trim()
        $FoundPid = 0
        if ([int]::TryParse($Contents,[ref]$FoundPid) -and $FoundPid -gt 0) {
            $ServerPid = $FoundPid
        }
    }
    $OurServers = New-Object System.Collections.ArrayList
    foreach ($Port in @(Candidate-Ports $Root $ServerPid)) {
        $Identified = Studio-On-Port $Port $Root
        if ($null -ne $Identified) { [void]$OurServers.Add($Identified) }
    }
    if ($OurServers.Count -eq 0) {
        # If the app process exists but has not started serving (or is hung), don't corrupt it.
        if ($ServerPid -gt 0 -and (Pid-Of-Our-App $ServerPid $Root)) {
            Stop-Patch ('The Studio backend PID ' + $ServerPid + ' is still running but does not answer the health check. Stop it after ensuring no tasks are running, then retry.')
        }
        Write-Host 'No running Studio backend detected; stale PID/foreign port will not block installation.' -ForegroundColor DarkCyan
        return
    }

    # An interruption of paid, local-render, or other in-flight tasks can lose progress.
    # The two read-only endpoints are used ONLY to decide whether the patch is safe to install.
    foreach ($Server in @($OurServers.ToArray())) {
        $Tasks = Local-Api $Server.port '/api/tasks/global'
        $Work = Local-Api $Server.port '/api/task-health/overview'
        if ($null -eq $Tasks -or !$Tasks.ok -or $null -eq $Tasks.data -or $null -eq $Work -or !$Work.ok -or $null -eq $Work.data) {
            Stop-Patch ('Studio is still running on port ' + $Server.port + ' but task status cannot be confirmed. To prevent data loss, no files were changed.')
        }
        $VideoCount = [int]$Tasks.data.active_count
        $WorkCount = [int]$Work.data.running_count
        if ($VideoCount -gt 0 -or $WorkCount -gt 0) {
            Stop-Patch ('There are active Studio tasks (video=' + $VideoCount + ', other=' + $WorkCount + '). Wait until they finish, then rerun the patch. No files changed.')
        }
    }

    foreach ($Server in @($OurServers.ToArray())) {
        Write-Host ('Studio V' + $Server.version + ' is idle on port ' + $Server.port + '. Stopping it safely before upgrading...') -ForegroundColor Yellow
        $Result = Local-Api $Server.port '/api/shutdown' 'POST'
        if ($null -eq $Result -or !$Result.ok) {
            Stop-Patch ('Graceful shutdown failed on port ' + $Server.port + '. No files changed.')
        }
    }
    foreach ($Server in @($OurServers.ToArray())) {
        $Gone = $false
        for ($i=0; $i -lt 40; $i++) {
            Start-Sleep -Milliseconds 500
            $StillRunning = Get-Process -Id $Server.pid -ErrorAction SilentlyContinue
            $StillServing = Studio-On-Port $Server.port $Root
            if ($null -eq $StillRunning -and $null -eq $StillServing) { $Gone = $true; break }
        }
        if (!$Gone) { Stop-Patch ('Studio on port ' + $Server.port + ' has not exited after 20 seconds. No files changed; do not force-kill an active paid request.') }
    }
    Write-Host 'Verified Studio is stopped. Starting safe patch installation.' -ForegroundColor Green
}

function Install {
  $PatchRoot = Split-Path -Parent $PSCommandPath
  $Root = App-Root $Target $PatchRoot
  $Manifest = Get-Content -LiteralPath (Join-Path $PatchRoot 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
  $Config = Join-Path $Root 'core/config.py'
  $Text = [IO.File]::ReadAllText($Config)
  if ($Text -notmatch '(?m)^\s*APP_VERSION\s*=\s*[''"](?<version>\d+\.\d+\.\d+)[''"]') { Stop-Patch 'Unable to detect source version; no files changed.' }
  $Version = $Matches.version
  Write-Host ('Seedance Studio V' + $Version + ' -> V' + $Manifest.target_version + ' cumulative patch') -ForegroundColor Cyan
  if ($Version -notin @($Manifest.supported_versions)) { Stop-Patch ('Source version not supported: ' + $Version + '. No files changed.') }
  $Actions = New-Object System.Collections.ArrayList
  foreach ($Entry in $Manifest.files) {
    $Rel=[string]$Entry.path
    if ([IO.Path]::IsPathRooted($Rel) -or $Rel -match '(^|[/\\])\.\.([/\\]|$)') { Stop-Patch 'Unsafe relative file path in manifest.' }
    $Src=Join-Path (Join-Path $PatchRoot 'payload') $Rel
    $Dest=Join-Path $Root $Rel
    $Want=[string]$Entry.sha256
    if ((Hash-Of $Src) -ne $Want) { Stop-Patch ('Payload checksum mismatch: '+$Rel) }
    $Actual=Hash-Of $Dest
    if ($Actual -eq $Want) { continue }
    if ($null -eq $Actual) {
      if (!$Entry.allow_missing) { Stop-Patch ('Required original file missing: '+$Rel+'. No files changed.') }
    } elseif ($Actual -notin @($Entry.allowed_old)) {
      Stop-Patch ('Unrecognized edited file: '+$Rel+'. To protect user modifications, no files changed.')
    }
    [void]$Actions.Add([pscustomobject]@{path=$Rel;old_sha256=$Actual;new_sha256=$Want})
  }
  if ($Actions.Count -eq 0) { Write-Host 'Already up to date, all files verified.' -ForegroundColor Green; return }
  Verify-Stopped $Root
  $Stamp=Get-Date -Format 'yyyyMMdd_HHmmss_fff'
  $Backup=Join-Path (Join-Path $Root 'patch_backups') ('v511_cumulative_'+$Stamp)
  New-Item -ItemType Directory -Force -Path $Backup | Out-Null
  $State=@{source_version=$Version;target_version='5.1.1';files=@($Actions.ToArray());created=(Get-Date).ToString('s')}
  Set-Content -LiteralPath (Join-Path $Backup 'rollback_state.json') -Value ($State|ConvertTo-Json -Depth 8) -Encoding UTF8
  foreach ($Change in $Actions) {
    if ($null -eq $Change.old_sha256) {continue}
    $Dest=Join-Path $Root $Change.path
    $Save=Join-Path $Backup $Change.path
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Save) | Out-Null
    Copy-Item -LiteralPath $Dest -Destination $Save -ErrorAction Stop
    if ((Hash-Of $Save) -ne $Change.old_sha256) { Stop-Patch ('Backup checksum failed: '+$Change.path) }
  }
  $Done=New-Object System.Collections.ArrayList
  try {
    foreach ($Change in $Actions) {
      $Dest=Join-Path $Root $Change.path
      $Src=Join-Path (Join-Path $PatchRoot 'payload') $Change.path
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Dest) | Out-Null
      [void]$Done.Add($Change)
      Copy-Item -LiteralPath $Src -Destination $Dest -Force -ErrorAction Stop
      if ((Hash-Of $Dest) -ne $Change.new_sha256) { throw ('Installed checksum failed: '+$Change.path) }
    }
    $Marker=Join-Path (Join-Path $Root 'patch_backups') 'last_511.txt'
    Set-Content -LiteralPath $Marker -Value $Backup -Encoding UTF8
  } catch {
    $Problem=$_.Exception.Message
    $RestoreErrors=@()
    foreach ($Change in @($Done.ToArray())) {
      try {
        $Dest=Join-Path $Root $Change.path
        if ($null -eq $Change.old_sha256) {if (Test-Path -LiteralPath $Dest) {Remove-Item -LiteralPath $Dest -Force}}
        else {Copy-Item -LiteralPath (Join-Path $Backup $Change.path) -Destination $Dest -Force}
      }catch{$RestoreErrors+=($Change.path+': '+$_.Exception.Message)}
    }
    if ($RestoreErrors.Count -gt 0) { Stop-Patch ('Installation failed and manual rollback may be required: '+$Problem+'; '+($RestoreErrors -join '; ')) }
    Stop-Patch ('Installation failed; files were restored: '+$Problem)
  }
  Write-Host ('SUCCESS: V'+$Version+' -> V5.1.1 | '+$Actions.Count+' files') -ForegroundColor Green
  Write-Host 'App settings, API keys, OSS, database, saved media and output files untouched.' -ForegroundColor Green
  Write-Host 'Official Hypit CLI is a separate official npm installation requiring network and explicit user approval.' -ForegroundColor Yellow
  Write-Host ('Backup: '+$Backup)
}
try { Install } catch {Write-Host ('[INSTALL FAILED] '+$_.Exception.Message) -ForegroundColor Red; exit 1}
