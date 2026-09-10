<#
.SYNOPSIS
    Runs offline chronological forecasting evaluation across all validated Phase 7B scenario datasets.

.DESCRIPTION
    Executes rolling-origin evaluation for Persistence, Moving Average, EWMA, and Linear Trend
    models across 1-step, 2-step, and 4-step horizons. Freezes parameters on the validation split
    and tests once on the held-out test split. Does not mutate or connect to Kubernetes.

.PARAMETER ConfigPath
    Path to forecasting YAML configuration. Defaults to experiments/configs/forecasting.yaml.

.PARAMETER DatasetRoot
    Root directory containing captured Phase 7B scenario runs. Defaults to data/raw/scenarios.

.PARAMETER OutputRoot
    Root directory for generated processed outputs. Defaults to data/processed/forecasting.
#>
[CmdletBinding()]
param (
    [string]$ConfigPath = "experiments/configs/forecasting.yaml",
    [string]$DatasetRoot = "data/raw/scenarios",
    [string]$OutputRoot = "data/processed/forecasting"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment Python executable not found at: $PythonExe"
}

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "KubePredict Phase 8A: Evaluating Forecasting Baselines" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Configuration: $ConfigPath"
Write-Host "Dataset Root:  $DatasetRoot"
Write-Host "Output Root:   $OutputRoot"
Write-Host "Kubernetes:    OFFLINE ONLY (No cluster mutation or queries)"
Write-Host ""

& $PythonExe -m forecasting.cli evaluate --config $ConfigPath --dataset-root $DatasetRoot --output-root $OutputRoot
if ($LASTEXITCODE -ne 0) {
    Write-Error "Forecasting evaluation failed with exit code $LASTEXITCODE"
}
