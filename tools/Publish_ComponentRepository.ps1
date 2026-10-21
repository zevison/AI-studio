# Local preparation is safe; real publication requires both explicit switches.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Destination,
    [string]$Ossutil='ossutil.exe',
    [switch]$Execute,
    [switch]$RedistributionCleared
)
$ErrorActionPreference='Stop'
if ($Destination -notmatch '^oss://[a-z0-9][a-z0-9-]+/[A-Za-z0-9_./-]+/$' -or $Destination.Contains('..')) {
    throw 'Destination must be a versioned oss://bucket/prefix/ path, without credentials.'
}
if ($Execute -and -not $RedistributionCleared) { throw 'Confirm binary redistribution rights before publication.' }
$inventory=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'inventory.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($inventory.format -ne 'seedance.component-repository@1') { throw 'Invalid repository inventory.' }
$base=[IO.Path]::GetFullPath($PSScriptRoot)+[IO.Path]::DirectorySeparatorChar
foreach ($row in $inventory.files) {
    $local=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot $row.path))
    if (-not $local.StartsWith($base,[StringComparison]::OrdinalIgnoreCase)) { throw 'Inventory path escaped repository.' }
    $item=Get-Item -LiteralPath $local
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Links are forbidden.' }
    if ($item.Length -ne $row.bytes -or (Get-FileHash -LiteralPath $local -Algorithm SHA256).Hash.ToLower() -ne $row.sha256) {
        throw ('Artifact changed: '+$row.path)
    }
}
# This script never reads/prints keys. Use an externally configured ossutil
# profile. Actual server HTTPS/CDN/public-read policy and costs need operator review.
if (-not $Execute) {
    Write-Output ('DRY RUN ONLY: '+$inventory.files.Count+' verified files -> '+$Destination)
    Write-Output 'No upload performed. Signed update metadata and client trust are not provisioned by this script.'
    return
}
$tool=Get-Command $Ossutil -ErrorAction Stop
foreach ($row in $inventory.files) {
    $local=Join-Path $PSScriptRoot $row.path
    & $tool.Source cp $local ($Destination+$row.path) --update
    if ($LASTEXITCODE -ne 0) { throw ('Upload failed; completed files retained. Exit '+$LASTEXITCODE) }
}
Write-Output 'Files uploaded. This does not enable trusted client updates or configure CDN permissions.'
