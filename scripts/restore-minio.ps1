<#
Restores a minio backup (from backup-minio.ps1) into the dev or prod minio
container. DESTRUCTIVE -- wipes existing /data first.

Usage: scripts\restore-minio.ps1 <dev|prod> <archive-file> [-Force]

-Force skips the "type the target to confirm" interactive prompt -- use
from a non-interactive shell only when you've already confirmed the target
and archive out of band.
#>
param(
    [Parameter(Mandatory=$true, Position=0)]
    [ValidateSet('dev','prod')]
    [string]$Target,

    [Parameter(Mandatory=$true, Position=1)]
    [string]$Archive,

    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

switch ($Target) {
    'dev'  { $composeArgs = @('compose','-f','docker-compose.yml') }
    'prod' { $composeArgs = @('compose','-p','aria-prod','-f','docker-compose.prod.yml','--env-file','.env.prod') }
}

if (-not (Test-Path $Archive -PathType Leaf)) {
    Write-Error "Usage: scripts\restore-minio.ps1 <dev|prod> <archive-file>"
    exit 1
}
$archiveItem = Get-Item $Archive

$cid = (& docker @composeArgs ps -q minio)
if (-not $cid) {
    Write-Error "minio isn't running for $Target -- start it first."
    exit 1
}

Write-Warning "This will WIPE all existing minio data in $Target and replace it with $Archive."
if ($Force) {
    Write-Host "-Force passed -- skipping confirmation."
} else {
    $confirm = Read-Host "Type '$Target' to confirm"
    if ($confirm -ne $Target) {
        Write-Error "Aborted."
        exit 1
    }
}

$archiveDir = $archiveItem.DirectoryName
$archiveName = $archiveItem.Name

docker run --rm --volumes-from $cid -v "${archiveDir}:/backup" alpine `
    sh -c "rm -rf /data/* && tar xzf /backup/$archiveName -C /data"

Write-Host "Restored $Archive into $Target."
