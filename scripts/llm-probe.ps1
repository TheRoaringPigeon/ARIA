<#
Scores the LLM decision steps (routing, action proposal, fuzzy entity
match, grounding gate, research tool choice) against the real model, using
synthetic cases from services/ai-service/probes/cases.py. Needs the aria-llm
stack running. Each run is saved to services/ai-service/probes/results/
(gitignored); -Compare diffs this run against a saved one, or 'last'.

Usage: scripts\llm-probe.ps1 [-Suite routing|action|fuzzy|gate|research|all] [-Compare last|<file>] [-OllamaHost http://localhost:11434]
#>

param(
    [ValidateSet('all', 'routing', 'action', 'fuzzy', 'gate', 'research')]
    [string]$Suite = 'all',
    [string]$Compare,
    [string]$OllamaHost = 'http://localhost:11434'
)

$ErrorActionPreference = 'Stop'

$serviceDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'services\ai-service'
$env:AI_SERVICE_OLLAMA_HOST = $OllamaHost

$probeArgs = @('--suite', $Suite)
if ($Compare) {
    if ($Compare -ne 'last') {
        $Compare = (Resolve-Path $Compare).Path
    }
    $probeArgs += @('--compare', $Compare)
}

Push-Location $serviceDir
try {
    uv run --package ai-service python -m probes @probeArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
