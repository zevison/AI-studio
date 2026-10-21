param([Parameter(Mandatory=$true)][string]$AppRoot)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Data = Join-Path $AppRoot 'data'
New-Item -ItemType Directory -Force -Path $Data | Out-Null
$State = Join-Path $Data 'hypit_official_install.state'
$Log = Join-Path $Data 'hypit_official_install.log'
function Note($Message) {
  $line = ('[' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '] ' + $Message)
  Add-Content -LiteralPath $Log -Value $line -Encoding UTF8
}
try {
  Set-Content -LiteralPath $State -Value 'running' -Encoding UTF8
  Note 'Start official Hypit distribution setup. No OpenLux requests will be made.'
  $node = Get-Command node.exe -ErrorAction SilentlyContinue
  $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
  if ($null -eq $node -or $null -eq $npm) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($null -eq $winget) { throw 'Node.js and winget are both missing. Cannot install Hypit automatically on this PC.' }
    Note 'Node LTS is required. Attempting official winget user installation.'
    & $winget.Source install --id OpenJS.NodeJS.LTS --exact --scope user --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-String | ForEach-Object {Note $_}
    $env:PATH = [Environment]::GetEnvironmentVariable('PATH','User') + ';' + [Environment]::GetEnvironmentVariable('PATH','Machine') + ';' + $env:PATH
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($null -eq $npm) { throw 'Node installed but npm is not available yet. Please restart the installer.' }
  }
  $ActualNode = (& node.exe --version 2>&1 | Out-String).Trim()
  if ($ActualNode -notmatch '^v(\d+)\.(\d+)\.(\d+)') { throw ('Cannot determine Node.js version: ' + $ActualNode) }
  $NodeMajor=[int]$Matches[1];$NodeMinor=[int]$Matches[2]
  if ($NodeMajor -lt 22 -or ($NodeMajor -eq 22 -and $NodeMinor -lt 15)) { throw ('Hypit 0.3.1 requires Node.js 22.15+. Detected '+$ActualNode+'. Update Node LTS and retry.') }
  $Prefix = Join-Path $AppRoot 'tools\hypit_runtime'
  New-Item -ItemType Directory -Force -Path $Prefix | Out-Null
  Note 'Installing official @hypit/hypit distribution with fixed version 0.3.1; no arbitrary GitHub HEAD code.'
  $NpmLog = Join-Path $Data 'hypit_npm_install.log'
  # Call npm.cmd through PowerShell's native-command runner; Start-Process on .cmd
  # can fail on some Windows hosts with NoNewWindow+redirect combinations.
  & $npm.Source install --prefix $Prefix --no-audit --no-fund '@hypit/hypit@0.3.1' *> $NpmLog
  $NpmExit = $LASTEXITCODE
  if ($NpmExit -ne 0) { throw ('Official npm install failed with exit ' + $NpmExit + '. Check hypit_npm_install.log.') }
  $exe = Join-Path $Prefix 'node_modules\.bin\hypit.cmd'
  if (!(Test-Path -LiteralPath $exe)) { throw 'Official hypit.cmd was not created by npm.' }
  $result = & $exe --version 2>&1 | Out-String
  Note ('Installed official Hypit executable. ' + $result.Trim())
  Set-Content -LiteralPath $State -Value 'installed' -Encoding UTF8
} catch {
  Note ('INSTALL FAILED: ' + $_.Exception.Message)
  Set-Content -LiteralPath $State -Value 'failed' -Encoding UTF8
  exit 1
}
