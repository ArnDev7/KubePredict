<#
.SYNOPSIS
    KubePredict Phase 10B: Validate Policy Configuration & Deterministic Arbitration Logic.
.DESCRIPTION
    Validates observer configuration, frozen forecaster, capacity configuration,
    forecast trust guard configuration, and policy arbitration configuration.
    Runs deterministic test examples for predictive mode, rapid-change override,
    reactive fallback, no-recommendation, cooldown, and downscale stabilization.
    Executes with zero cluster mutation and zero network calls.
#>
param(
    [string]$ConfigPath = "config/observer.yaml",
    [string]$CapacityConfigPath = "controller/config/capacity.yaml",
    [string]$TrustGuardConfigPath = "controller/config/trust_guard.yaml",
    [string]$PolicyConfigPath = "controller/config/policy.yaml",
    [string]$FrozenForecasterPath = "forecasting/config/final_forecaster.yaml"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "KubePredict Phase 10B: Validate Policy & Arbitration" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Policy Config:      $PolicyConfigPath"
Write-Host "Trust Guard Config: $TrustGuardConfigPath"
Write-Host "Capacity Config:    $CapacityConfigPath"
Write-Host "Observer Config:    $ConfigPath"
Write-Host "Frozen Forecaster:  $FrozenForecasterPath"
Write-Host "Mode:               OFFLINE STATIC VALIDATION (Zero network, Zero K8s)"
Write-Host ""

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}

# Run controller CLI validate-arbitration
& $PythonExe -m controller.cli validate-arbitration `
    --config $ConfigPath `
    --capacity-config $CapacityConfigPath `
    --trust-guard-config $TrustGuardConfigPath `
    --policy-config $PolicyConfigPath `
    --frozen-forecaster $FrozenForecasterPath

if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[FAIL] Arbitration validation failed." -ForegroundColor Red
    exit 1
}

Write-Host "`n[SUCCESS] Arbitration policy and deterministic logic fully validated." -ForegroundColor Green
