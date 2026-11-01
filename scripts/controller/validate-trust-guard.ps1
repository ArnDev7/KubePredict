<#
.SYNOPSIS
    KubePredict Phase 10A: Validate Forecast Trust Guard Configuration & Logic.
.DESCRIPTION
    Validates frozen forecaster, capacity configuration, and trust guard configuration.
    Displays threshold source, threshold values, and status.
    Runs deterministic known-history test examples with zero cluster mutation and zero network calls.
#>
param(
    [string]$ConfigPath = "config/observer.yaml",
    [string]$CapacityConfigPath = "controller/config/capacity.yaml",
    [string]$TrustGuardConfigPath = "controller/config/trust_guard.yaml",
    [string]$FrozenForecasterPath = "forecasting/config/final_forecaster.yaml"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "KubePredict Phase 10A: Validate Forecast Trust Guard" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
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

# Run controller CLI validate-trust-guard
& $PythonExe -m controller.cli validate-trust-guard `
    --config $ConfigPath `
    --capacity-config $CapacityConfigPath `
    --trust-guard-config $TrustGuardConfigPath `
    --frozen-forecaster $FrozenForecasterPath

if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[FAIL] Trust Guard validation failed." -ForegroundColor Red
    exit 1
}

Write-Host "`n[SUCCESS] Forecast Trust Guard fully validated." -ForegroundColor Green
