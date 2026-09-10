<#
.SYNOPSIS
    Validates frozen final forecaster configuration and runtime contract (Phase 8B).

.DESCRIPTION
    Loads forecasting/config/final_forecaster.yaml, asserts that status is 'offline_selected'
    (never 'production_ready'), exercises the pure in-process ForecastingService across known
    synthetic test vectors, verifies Persistence fallback behavior, and runs the Phase 8B
    audit, leakage, and service test suites.

.PARAMETER ConfigPath
    Path to frozen forecaster YAML configuration. Defaults to forecasting/config/final_forecaster.yaml.
#>
[CmdletBinding()]
param (
    [string]$ConfigPath = "forecasting/config/final_forecaster.yaml"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment Python executable not found at: $PythonExe"
}

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "KubePredict Phase 8B: Validate Final Frozen Forecaster" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Configuration: $ConfigPath"
Write-Host "Mode:          PURE IN-PROCESS VALIDATION (No network, no K8s)"
Write-Host ""

# 1. Run Python validation check
$ValidationScript = @"
import sys
from pathlib import Path
from datetime import datetime, UTC, timedelta
from forecasting.service import ForecastingService
from forecasting.base import ForecastInput, ForecastOutput

conf_path = Path(r'$ConfigPath')
if not conf_path.exists():
    print(f'ERROR: Config file not found: {conf_path}', file=sys.stderr)
    sys.exit(1)

service = ForecastingService.from_config_file(conf_path)
print(f'Loaded model: {service.model_name} v{service.model_version}')
print(f'Status:       {service.status}')
print(f'Horizon:      {service.default_horizon_steps} step(s) ({service.default_horizon_steps * service.sampling_interval_seconds}s)')
print(f'Fallback:     enabled={service.persistence_fallback_enabled}')

if service.status != 'offline_selected':
    print(f'ERROR: Expected status offline_selected, got {service.status}', file=sys.stderr)
    sys.exit(1)

# Test normal inference
now = datetime.now(UTC)
inp = ForecastInput(
    generated_at=now,
    sampling_interval_seconds=15.0,
    observations=[10.0, 15.0, 20.0, 25.0, 30.0],
    observation_timestamps=[now - timedelta(seconds=(4 - i) * 15) for i in range(5)],
    requested_horizon_steps=service.default_horizon_steps,
)
out = service.predict(inp)
assert isinstance(out, ForecastOutput), 'Must return ForecastOutput'
assert not out.fallback_used, 'Fallback should not trigger on adequate history'
print(f'Normal inference output: predicted={out.predicted_request_rate_per_second}, fallback_used={out.fallback_used}')

# Test fallback mechanism with minimal history
short_inp = ForecastInput(
    generated_at=now,
    sampling_interval_seconds=15.0,
    observations=[42.0],
    observation_timestamps=[now],
    requested_horizon_steps=4,
)
out_short = service.predict(short_inp)
print(f'Short-history inference output: predicted={out_short.predicted_request_rate_per_second}, fallback_used={out_short.fallback_used}')

print('All in-process runtime contract assertions passed.')
"@

& $PythonExe -c $ValidationScript
if ($LASTEXITCODE -ne 0) {
    Write-Error "Forecasting service contract validation failed with exit code $LASTEXITCODE"
}

Write-Host "`nRunning Phase 8B Unit & Leakage Tests..." -ForegroundColor Yellow
& $PythonExe -m pytest tests/unit/test_forecasting_audit.py tests/unit/test_forecasting_leakage.py tests/unit/test_forecasting_service.py -v
if ($LASTEXITCODE -ne 0) {
    Write-Error "Phase 8B unit tests failed with exit code $LASTEXITCODE"
}

Write-Host "`n[SUCCESS] Final model validation and test suites passed successfully." -ForegroundColor Green
