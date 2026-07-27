<#
Dumps the `aria` database from the dev or prod Mongo container into
backups/mongo-<target>-<timestamp>.gz (mongodump --archive --gzip).

Usage: scripts\backup-mongo.ps1 <dev|prod>
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

$backupsDir = Join-Path $repoRoot 'backups'
New-Item -ItemType Directory -Force -Path $backupsDir | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$out = Join-Path $backupsDir "mongo-$Target-$timestamp.gz"

# Start-Process redirects stdout as raw bytes -- piping through the
# PowerShell pipeline instead would mangle mongodump's binary output.
$argList = $composeArgs + @('exec','-T','mongo','mongodump','--archive','--gzip','--db','aria')
$proc = Start-Process -FilePath 'docker' -ArgumentList $argList -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput $out -WorkingDirectory $repoRoot
if ($proc.ExitCode -ne 0) {
    Write-Error "mongodump failed with exit code $($proc.ExitCode)"
    exit 1
}

Write-Host "Wrote $out"
