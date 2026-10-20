#requires -Version 5.1
# Fallback for computers where Windows Script Host is unavailable or disabled.
$ErrorActionPreference='Stop'
try {
    $root=Split-Path -Parent $PSScriptRoot
    $python=Join-Path $root '.venv\Scripts\python.exe'
    $script=Join-Path $PSScriptRoot 'start_silent.py'
    Start-Process -FilePath $python -ArgumentList ('"'+$script+'"') -WorkingDirectory $root -WindowStyle Hidden -ErrorAction Stop | Out-Null
    exit 0
} catch {
    Write-Host ('Could not start Studio: '+$_.Exception.Message)
    exit 14
}
