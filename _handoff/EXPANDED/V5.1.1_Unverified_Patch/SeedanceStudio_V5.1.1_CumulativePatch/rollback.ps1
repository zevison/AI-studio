param([string]$Target='')
$ErrorActionPreference='Stop'
function Hash-Of([string]$Path){if (!(Test-Path -LiteralPath $Path -PathType Leaf)){return $null}; return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
function Rollback {
  $PatchRoot=Split-Path -Parent $PSCommandPath
  if (!$Target) {$Target=Split-Path -Parent $PatchRoot}
  $Root=(Resolve-Path -LiteralPath $Target -ErrorAction Stop).Path
  $Config=(Get-Content -LiteralPath (Join-Path $Root 'core/config.py') -Raw -Encoding UTF8)
  if ($Config -notmatch '(?m)^\s*APP_VERSION\s*=\s*[''"]5\.1\.1[''"]') {throw 'Only patched V5.1.1 may be rolled back.'}
  $Pid=Join-Path $Root 'seedance_studio.pid'
  if(Test-Path -LiteralPath $Pid){$Number=0
    if([int]::TryParse(((Get-Content $Pid -Raw).Trim()),[ref]$Number) -and $Number -gt 0){
      $Proc=Get-CimInstance Win32_Process -Filter ('ProcessId='+$Number) -ErrorAction SilentlyContinue
      if($Proc -and ([string]$Proc.CommandLine).IndexOf((Join-Path $Root 'app.py'),[StringComparison]::OrdinalIgnoreCase) -ge 0){throw 'Close Studio before rollback to protect tasks.'}
    }
  }
  $Marker=Join-Path (Join-Path $Root 'patch_backups') 'last_511.txt'
  if(!(Test-Path -LiteralPath $Marker)){throw 'No V5.1.1 patch backup marker found.'}
  $Backup=(Get-Content -LiteralPath $Marker -Raw -Encoding UTF8).Trim()
  $State=Get-Content -LiteralPath (Join-Path $Backup 'rollback_state.json') -Raw -Encoding UTF8|ConvertFrom-Json
  if($State.target_version -ne '5.1.1'){throw 'Backup target-version mismatch.'}
  foreach($Item in $State.files){
    if((Hash-Of (Join-Path $Root $Item.path)) -ne $Item.new_sha256){throw ('Installed file edited: '+$Item.path)}
    if($null -ne $Item.old_sha256 -and (Hash-Of (Join-Path $Backup $Item.path)) -ne $Item.old_sha256){throw ('Backup damaged: '+$Item.path)}
  }
  foreach($Item in $State.files){
    $Dest=Join-Path $Root $Item.path
    if($null -eq $Item.old_sha256){Remove-Item -LiteralPath $Dest -Force}
    else{Copy-Item -LiteralPath (Join-Path $Backup $Item.path) -Destination $Dest -Force}
  }
  Remove-Item -LiteralPath $Marker -Force
  Write-Host ('SUCCESS: restored V'+$State.source_version+' app files; user data untouched.') -ForegroundColor Green
}
try {Rollback} catch {Write-Host ('[ROLLBACK FAILED] '+$_.Exception.Message) -ForegroundColor Red;exit 1}
