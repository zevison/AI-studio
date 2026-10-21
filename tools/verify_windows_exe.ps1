#requires -Version 5.1
# Smoke test the true Windows EXE installer on a disposable Windows machine.
# Checks non-destructive install/reinstall/uninstall. NOT a physical-PC acceptance.
param([Parameter(Mandatory=$true)][string]$Installer)
$ErrorActionPreference='Stop'
$source=(Resolve-Path -LiteralPath $Installer).Path
$bytes=[IO.File]::ReadAllBytes($source)
if($bytes.Length -lt 65536 -or $bytes[0] -ne 77 -or $bytes[1] -ne 90){throw 'Installer is not a Windows executable'}
$target=Join-Path $env:RUNNER_TEMP ('SeedanceSmoke_'+[Guid]::NewGuid().ToString('N'))
$logs=Join-Path $env:RUNNER_TEMP ('SeedanceSmokeLogs_'+[Guid]::NewGuid().ToString('N'))
New-Item -Path $logs -ItemType Directory -Force | Out-Null
$log=Join-Path $logs 'install.log'
function Install {
    $args=@('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',('/DIR='+$target),('/LOG='+$log))
    $p=Start-Process -FilePath $source -ArgumentList $args -PassThru
    if(-not $p.WaitForExit(240000)){Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue;throw 'EXE installation timed out'}
    if($p.ExitCode -ne 0){throw ('EXE installation failed with '+$p.ExitCode)}
}
try {
    Install
    foreach($path in @('00_START.cmd','01_INSTALL.cmd','app.py','core\config.py','tools\safe_migrate.py','tools\start_silent.py')){
        if(-not (Test-Path -LiteralPath (Join-Path $target $path) -PathType Leaf)){throw ('Missing installed input: '+$path)}
    }
    $data=Join-Path $target 'data'; New-Item -ItemType Directory -Path $data -Force | Out-Null
    $marker=Join-Path $data 'user-owned-smoke.txt'
    [IO.File]::WriteAllText($marker,'preserve-me')
    Install
    if([IO.File]::ReadAllText($marker) -ne 'preserve-me'){throw 'Install/reinstall damaged existing user data'}
    $uninstaller=Join-Path $target 'unins000.exe'
    if(-not (Test-Path $uninstaller)){throw 'Missing uninstaller'}
    $p=Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -PassThru
    if(-not $p.WaitForExit(120000)){Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue;throw 'Uninstall timed out'}
    if($p.ExitCode -ne 0){throw ('Uninstall failed: '+$p.ExitCode)}
    if(-not (Test-Path -LiteralPath $marker)){throw 'Uninstall unexpectedly deleted user data'}
    Write-Host 'PASS: genuine EXE; Windows silent install, reinstall and uninstall preserve user data marker.'
} finally {
    Write-Host ('Smoke logs: '+$logs)
    # Do not destroy any remaining user marker or diagnostic files.
}
