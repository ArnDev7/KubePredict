<#
.SYNOPSIS
    Runs independent forecasting audit, robustness analysis, and selection checks (Phase 8B).

.DESCRIPTION
    Audits the Phase 8A offline forecasting evaluation for temporal leakage, data immutability,
    chronological split integrity, independent metric reproduction, scenario robustness,
    and parameter sensitivity. Freezes selection decisions into reproducible artifacts.
    Guarantees zero Kubernetes cluster mutation and preserves Phase 7B raw telemetry.

.PARAMETER EvaluationDir
    Directory containing Phase 8A evaluation artifacts to audit.
    Defaults to data/processed/forecasting/eval-20260910_180143.

.PARAMETER ConfigPath
    Path to evaluation configuration file. Defaults to experiments/configs/forecasting.yaml.

.PARAMETER DatasetRoot
    Root directory containing captured Phase 7B scenario datasets. Defaults to data/raw/scenarios.

.PARAMETER OutputRoot
    Output root for audit summary reports and artifacts. Defaults to results/summaries/forecasting-audit.

.PARAMETER Strict
    If specified, exits with error if any audit check fails.
#>
[CmdletBinding()]
param (
    [string]$EvaluationDir = "data/processed/forecasting/eval-20260910_180143",
    [string]$ConfigPath = "experiments/configs/forecasting.yaml",
    [string]$DatasetRoot = "data/raw/scenarios",
    [string]$OutputRoot = "results/summaries/forecasting-audit",
    [switch]$Strict
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment Python executable not found at: $PythonExe"
}

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "KubePredict Phase 8B: Forecasting Audit & Selection Analysis" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Evaluation Dir: $EvaluationDir"
Write-Host "Config Path:    $ConfigPath"
Write-Host "Dataset Root:   $DatasetRoot"
Write-Host "Output Root:    $OutputRoot"
Write-Host "Mode:           OFFLINE AUDIT (Zero Kubernetes mutation)"
Write-Host ""

$AuditArgs = @(
    "-m", "forecasting.cli", "audit",
    "--evaluation-dir", $EvaluationDir,
    "--config", $ConfigPath,
    "--dataset-root", $DatasetRoot,
    "--output-root", $OutputRoot
)

if ($Strict) {
    $AuditArgs += "--strict"
}

& $PythonExe $AuditArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "Forecasting audit failed with exit code $LASTEXITCODE"
}

Write-Host "`n[SUCCESS] Phase 8B audit and selection analysis completed successfully." -ForegroundColor Green
