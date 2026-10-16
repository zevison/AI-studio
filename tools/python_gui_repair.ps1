#requires -Version 5.1
# Intentionally interactive: only verified official Python installer.
$ErrorActionPreference='Stop'
$installer='python-3.12.10-amd64.exe'
if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {$installer='python-3.12.10-arm64.exe'}
$root=Split-Path -Parent $PSScriptRoot
$manual=Join-Path (Join-Path $root 'installers') $installer
$cached=Join-Path $env:LOCALAPPDATA ('SeedanceStudio\downloads\'+$installer)
$path = if (Test-Path -LiteralPath $manual) {$manual} elseif (Test-Path -LiteralPath $cached) {$cached} else {$null}
if ($null -eq $path) {
 Write-Host 'Opening official Python download page.'
 Start-Process 'https://www.python.org/downloads/release/python-31210/'
 exit 2
}
$sig=Get-AuthenticodeSignature -LiteralPath $path
if ($sig.Status -ne 'Valid' -or $null -eq $sig.SignerCertificate -or $sig.SignerCertificate.Subject -notmatch 'Python Software Foundation') {throw 'Refusing unverified Python installer.'}
Write-Host 'Starting official Python installer in interactive GUI mode.'
Write-Host 'If Modify/Repair appears, choose Repair and install python.exe, standard library, pip.'
Write-Host 'For new setup, install for the CURRENT Windows account.'
$proc=Start-Process -FilePath $path -PassThru -Wait
Write-Host ('Installer closed. Exit code: '+$proc.ExitCode)
