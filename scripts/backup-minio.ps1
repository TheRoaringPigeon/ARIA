<#
Tars the dev or prod minio container's /data (the aria-documents bucket
and everything else in that MinIO instance) into
backups/minio-<target>-<timestamp>.tar.gz.

Usage: scripts\backup-minio.ps1 <dev|prod>
#>
param(
    [Parameter(Mandatory=$true, Position=0)]
    [ValidateSet('dev','prod')]
    [string]$Target
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

switch ($Target) {
    'dev'  { $composeArgs = @('compose','-f','docker-compose.yml') }
    'prod' { $composeArgs = @('compose','-p','aria-prod','-f','docker-compose.prod.yml','--env-file','.env.prod') }
}

$cid = (& docker @composeArgs ps -q minio)
if (-not $cid) {
    Write-Error "minio isn't running for $Target -- start it first."
    exit 1
}

$backupsDir = Join-Path $repoRoot 'backups'
New-Item -ItemType Directory -Force -Path $backupsDir | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$out = "minio-$Target-$timestamp.tar.gz"

# --volumes-from inherits whatever volume the running container has mounted
# at /data, regardless of the actual Docker volume name (which differs
# between dev/prod) -- no need to know or guess it here.
docker run --rm --volumes-from $cid -v "${backupsDir}:/backup" alpine `
    tar czf "/backup/$out" -C /data .

Write-Host "Wrote backups/$out"
