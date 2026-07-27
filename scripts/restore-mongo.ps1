<#
Restores a mongodump archive (from backup-mongo.ps1) into the dev or prod
Mongo container. DESTRUCTIVE -- drops the existing `aria` database first.

Usage: scripts\restore-mongo.ps1 <dev|prod> <archive-file>
#>
param(
    [Parameter(Mandatory=$true, Position=0)]
    [ValidateSet('dev','prod')]
    [string]$Target,

    [Parameter(Mandatory=$true, Position=1)]
    [string]$Archive
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

switch ($Target) {
    'dev'  { $composeArgs = @('compose','-f','docker-compose.yml') }
    'prod' { $composeArgs = @('compose','-p','aria-prod','-f','docker-compose.prod.yml','--env-file','.env.prod') }
}

if (-not (Test-Path $Archive -PathType Leaf)) {
    Write-Error "Usage: scripts\restore-mongo.ps1 <dev|prod> <archive-file>"
    exit 1
}
$archiveFullPath = (Resolve-Path $Archive).Path

Write-Warning "This will DROP the existing 'aria' database in $Target and replace it with $Archive."
$confirm = Read-Host "Type '$Target' to confirm"
if ($confirm -ne $Target) {
    Write-Error "Aborted."
    exit 1
}

# Start-Process redirects stdin as raw bytes -- piping through the
# PowerShell pipeline instead would mangle the gzip archive's binary data.
$argList = $composeArgs + @('exec','-T','mongo','mongorestore','--archive','--gzip','--drop')
$proc = Start-Process -FilePath 'docker' -ArgumentList $argList -NoNewWindow -Wait -PassThru `
    -RedirectStandardInput $archiveFullPath -WorkingDirectory $repoRoot
if ($proc.ExitCode -ne 0) {
    Write-Error "mongorestore failed with exit code $($proc.ExitCode)"
    exit 1
}

Write-Host "Restored $Archive into $Target."
