<#
Scores the LLM decision steps (routing, action proposal, fuzzy entity
match) against the real model, using synthetic cases from
services/ai-service/probes/cases.py. Needs the aria-llm stack running.

Usage: scripts\llm-probe.ps1 [-Suite routing|action|fuzzy|all] [-OllamaHost http://localhost:11434]
#>

param(
    [ValidateSet('all', 'routing', 'action', 'fuzzy')]
    [string]$Suite = 'all',
    [string]$OllamaHost = 'http://localhost:11434'
)

$ErrorActionPreference = 'Stop'

$serviceDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'services\ai-service'
$env:AI_SERVICE_OLLAMA_HOST = $OllamaHost

Push-Location $serviceDir
try {
    uv run --package ai-service python -m probes --suite $Suite
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
