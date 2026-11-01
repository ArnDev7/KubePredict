<#
.SYNOPSIS
    KubePredict Phase 9B: Validate Capacity Model and Configuration.
.DESCRIPTION
    Validates controller capacity configuration, displays effective capacity,
    and runs deterministic calculation test examples.
    Runs 100% offline with zero cluster mutation and zero network calls.
#>
param(
    [string]$ConfigPath = "controller/config/capacity.yaml",
    [string]$ObserverConfigPath = "config/observer.yaml",
    [string]$FrozenForecasterPath = "forecasting/config/final_forecaster.yaml"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "KubePredict Phase 9B: Validate Capacity Model & Configuration" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Capacity Config:   $ConfigPath"
Write-Host "Observer Config:   $ObserverConfigPath"
Write-Host "Frozen Forecaster: $FrozenForecasterPath"
Write-Host "Mode:              OFFLINE STATIC VALIDATION (Zero network, Zero K8s)"
Write-Host ""

$PythonExe = Join-Path $PSScriptRoot "..\..\.venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}

# 1. Run controller CLI validate
& $PythonExe -m controller.cli validate --config $ObserverConfigPath --capacity-config $ConfigPath --frozen-forecaster $FrozenForecasterPath
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[FAIL] Controller CLI validation failed." -ForegroundColor Red
    exit 1
}

# 2. Run deterministic calculation test suite via Python one-liner
Write-Host "`n--- DETERMINISTIC CAPACITY CALCULATION EXAMPLES ---" -ForegroundColor Yellow
$ValidationCode = @"
from controller.capacity_config import CapacityConfig
from controller.capacity import calculate_capacity_recommendation

cfg = CapacityConfig.from_yaml_file('$ConfigPath')
eff = cfg.effective_capacity_per_replica
print(f'Effective Capacity per Replica: {eff} req/s')

test_cases = [
    ('Zero demand', 0.0, 2, 0, 1, 1, 'scale_down', 'minimum_bound'),
    ('Low demand (< 1 rep)', 10.0, 2, 1, 1, 1, 'scale_down', 'forecast_demand'),
    ('Exact 1 rep capacity', 15.75, 2, 1, 1, 1, 'scale_down', 'forecast_demand'),
    ('Fractional ceiling (16 req/s)', 16.0, 2, 2, 2, 2, 'no_change', 'no_change_required'),
    ('Moderate scale-up', 45.0, 2, 3, 3, 3, 'scale_up', 'forecast_demand'),
    ('Step-limited scale-up', 100.0, 2, 7, 7, 5, 'scale_up', 'scale_up_step_limited'),
    ('Max bound + step limit', 300.0, 2, 20, 10, 5, 'scale_up', 'scale_up_step_limited'),
    ('Step-limited scale-down', 0.0, 5, 0, 1, 4, 'scale_down', 'scale_down_step_limited'),
]

all_passed = True
for name, rate, cur, exp_uncon, exp_prop, exp_final, exp_dir, exp_reason in test_cases:
    res = calculate_capacity_recommendation(rate, cur, cfg)
    ok = (
        res.unconstrained_required_replicas == exp_uncon
        and res.proposed_replicas == exp_prop
        and res.final_replicas == exp_final
        and res.direction.value == exp_dir
        and res.reason.value == exp_reason
    )
    status = '[PASS]' if ok else '[FAIL]'
    if not ok:
        all_passed = False
    print(f'{status} {name}: rate={rate}, cur={cur} -> uncon={res.unconstrained_required_replicas}, prop={res.proposed_replicas}, final={res.final_replicas}, dir={res.direction.value}, reason={res.reason.value}')

if not all_passed:
    raise SystemExit(1)
print('\n[SUCCESS] All deterministic capacity calculation examples matched exact expectations.')
"@

& $PythonExe -c $ValidationCode
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[FAIL] Deterministic capacity validation examples failed." -ForegroundColor Red
    exit 1
}

Write-Host "`n[SUCCESS] Capacity model and configuration fully validated." -ForegroundColor Green
exit 0
