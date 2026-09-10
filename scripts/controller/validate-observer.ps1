<#
.SYNOPSIS
    Validates observe-only controller configuration and static write audit (Phase 9A).

.DESCRIPTION
    Validates controller/config.py settings against forecasting/config/final_forecaster.yaml,
    asserts mode is 'observe' (not live/dry_run), runs the static write-capability audit,
    and contacts zero cluster APIs or network services.

.PARAMETER ConfigPath
    Path to controller YAML configuration. Defaults to config/observer.yaml.

.PARAMETER FrozenForecasterPath
    Path to frozen forecaster configuration. Defaults to forecasting/config/final_forecaster.yaml.
#>
[CmdletBinding()]
param (
    [string]$ConfigPath = "config/observer.yaml",
    [string]$FrozenForecasterPath = "forecasting/config/final_forecaster.yaml"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment Python executable not found at: $PythonExe"
}

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "KubePredict Phase 9A: Validate Observe-Only Controller" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Config Path:       $ConfigPath"
Write-Host "Frozen Forecaster: $FrozenForecasterPath"
Write-Host "Mode:              OFFLINE STATIC VALIDATION (Zero network, Zero K8s)"
Write-Host ""

& $PythonExe -m controller.cli validate --config $ConfigPath --frozen-forecaster $FrozenForecasterPath
if ($LASTEXITCODE -ne 0) {
    Write-Error "Observer configuration validation failed with exit code $LASTEXITCODE"
}

Write-Host "`n[SUCCESS] Controller configuration and static write-capability checks passed." -ForegroundColor Green
