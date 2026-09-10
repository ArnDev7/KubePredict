<#
.SYNOPSIS
    Validates Phase 7B scenario datasets and forecasting configuration without executing predictions.

.DESCRIPTION
    Inspects telemetry.csv across all validated Phase 7B scenario runs, verifies chronological
    sample counts, interval consistency, missing target values, and confirms that all required
    scenarios meet forecasting split thresholds.

.PARAMETER ConfigPath
    Path to forecasting YAML configuration. Defaults to experiments/configs/forecasting.yaml.

.PARAMETER DatasetRoot
    Root directory containing captured Phase 7B scenario runs. Defaults to data/raw/scenarios.
#>
[CmdletBinding()]
param (
    [string]$ConfigPath = "experiments/configs/forecasting.yaml",
    [string]$DatasetRoot = "data/raw/scenarios"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment Python executable not found at: $PythonExe"
}

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "KubePredict Phase 8A: Validating Forecasting Configuration & Data" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Configuration: $ConfigPath"
Write-Host "Dataset Root:  $DatasetRoot"
Write-Host ""

& $PythonExe -m forecasting.cli validate --config $ConfigPath --dataset-root $DatasetRoot
if ($LASTEXITCODE -ne 0) {
    Write-Error "Forecasting data validation failed with exit code $LASTEXITCODE"
}
