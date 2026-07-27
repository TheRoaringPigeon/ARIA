<#
Logs in as the "Claude" MCP service account and prints a fresh
aria_session cookie value on stdout (nothing else -- safe to capture in
a variable). Reads credentials from .env.aria-mcp at the repo root.

Usage: $sessionCookie = scripts\aria-mcp-login.ps1
#>

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repoRoot '.env.aria-mcp'

if (-not (Test-Path $envFile)) {
    Write-Error "Missing $envFile -- see conversation history for how it was created."
    exit 1
}

$envVars = @{}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*#' -or $_ -match '^\s*$') { return }
    if ($_ -match '^\s*([^=]+)=(.*)$') {
        $envVars[$matches[1].Trim()] = $matches[2].Trim().Trim('"')
    }
}

$body = @{ email = $envVars['ARIA_MCP_EMAIL']; password = $envVars['ARIA_MCP_PASSWORD'] } | ConvertTo-Json

try {
    $response = Invoke-WebRequest -Uri "$($envVars['ARIA_CORE_API_URL'])/auth/login" -Method Post `
        -ContentType 'application/json' -Body $body -UseBasicParsing
} catch {
    $errorBody = $null
    if ($_.Exception.Response) {
        $stream = $_.Exception.Response.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream)
        $errorBody = $reader.ReadToEnd()
    }
    Write-Error "Login failed -- response was:`n$errorBody"
    exit 1
}

$setCookie = ($response.Headers['Set-Cookie'] -join '; ')
$cookie = $null
if ($setCookie -match 'aria_session=([^;]+)') {
    $cookie = $matches[1]
}

if (-not $cookie) {
    Write-Error "Login failed -- response was:`n$($response.Content)"
    exit 1
}

Write-Output $cookie
