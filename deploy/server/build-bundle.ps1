[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path 'dist\server')
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$expectedPrefix = (Join-Path $repositoryRoot 'dist') + [IO.Path]::DirectorySeparatorChar
$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory)

if (-not $resolvedOutput.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory must be below $expectedPrefix"
}

$required = @(
    (Join-Path $repositoryRoot 'src\universal_agent_runtime'),
    (Join-Path $repositoryRoot 'agent_image'),
    (Join-Path $repositoryRoot 'ui'),
    (Join-Path $PSScriptRoot 'pyproject.toml'),
    (Join-Path $PSScriptRoot '.env.example'),
    (Join-Path $PSScriptRoot 'build-agent-image.sh'),
    (Join-Path $PSScriptRoot 'universal-agent-runtime.service'),
    (Join-Path $PSScriptRoot 'universal-agent-runtime-ui.service')
)
foreach ($path in $required) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Missing required deployment input: $path"
    }
}

if (Test-Path -LiteralPath $resolvedOutput) {
    Remove-Item -LiteralPath $resolvedOutput -Recurse -Force
}
New-Item -ItemType Directory -Path (Join-Path $resolvedOutput 'src') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'pyproject.toml') -Destination $resolvedOutput
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '.env.example') -Destination $resolvedOutput
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination $resolvedOutput
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'run-orchestrator.sh') -Destination $resolvedOutput
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'run-ui.sh') -Destination $resolvedOutput
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'build-agent-image.sh') -Destination $resolvedOutput
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'universal-agent-runtime.service') -Destination $resolvedOutput
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'universal-agent-runtime-ui.service') -Destination $resolvedOutput
Copy-Item -LiteralPath (Join-Path $repositoryRoot 'src\universal_agent_runtime') -Destination (Join-Path $resolvedOutput 'src') -Recurse
Copy-Item -LiteralPath (Join-Path $repositoryRoot 'agent_image') -Destination $resolvedOutput -Recurse
Copy-Item -LiteralPath (Join-Path $repositoryRoot 'ui') -Destination $resolvedOutput -Recurse
Get-ChildItem -LiteralPath $resolvedOutput -Recurse -Directory -Force |
    Where-Object { $_.Name -in @('__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache') } |
    Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $resolvedOutput -Recurse -File -Force |
    Where-Object { $_.Extension -in @('.pyc', '.pyo') } |
    Remove-Item -Force

Write-Output "Deployment bundle created: $resolvedOutput"
