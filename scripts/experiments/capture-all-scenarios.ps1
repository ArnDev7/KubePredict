# Phase 7B Sequential Scenario Capture Orchestration
param (
    [int]$RecoveryIntervalSeconds = 45,
    [int]$WorkloadPort = 18000,
    [int]$PrometheusPort = 19090
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  Phase 7B: Sequential Scenario Capture & Telemetry Export " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$scenarios = @("steady", "ramp", "periodic", "sudden_increase", "drift")
$results = @()

foreach ($idx in 0..($scenarios.Count - 1)) {
    $scenario = $scenarios[$idx]
    $stepNum = $idx + 1
    Write-Host "`n>>> Step $stepNum/5: Executing Scenario '$scenario'..." -ForegroundColor Magenta

    $startTime = Get-Date

    # Execute single scenario script
    & ".\scripts\experiments\capture-scenario.ps1" -Scenario $scenario -WorkloadPort $WorkloadPort -PrometheusPort $PrometheusPort
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        Write-Error "Execution of scenario '$scenario' failed! Stopping batch capture."
        exit $exitCode
    }

    $endTime = Get-Date
    $duration = ($endTime - $startTime).TotalSeconds

    $results += [PSCustomObject]@{
        Scenario = $scenario
        Status = "Success"
        DurationSeconds = [math]::Round($duration, 1)
    }

    # If not the last scenario, wait for the recovery interval
    if ($idx -lt ($scenarios.Count - 1)) {
        Write-Host "`nWaiting $RecoveryIntervalSeconds seconds recovery interval before next scenario..." -ForegroundColor Gray
        Start-Sleep -Seconds $RecoveryIntervalSeconds

        # Verify cluster health during recovery
        $readyPods = (kubectl get pods -n kubepredict-system -l app.kubernetes.io/component=workload-api --context kind-kubepredict-dev --no-headers | Where-Object { $_ -match "Running" }).Count
        if ($readyPods -ne 2) {
            Write-Error "Workload health degraded during recovery! Expected 2 Running pods, found $readyPods."
            exit 1
        }
    }
}

Write-Host "`n==========================================================" -ForegroundColor Green
Write-Host "  All 5 Scenarios Completed Successfully!                 " -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green

$results | Format-Table -AutoSize

# Build dataset index
Write-Host "`nGenerating dataset-index.json..." -ForegroundColor Yellow
$pyIndexScript = @'
import json
from pathlib import Path
from datetime import datetime

raw_root = Path('data/raw/scenarios')
index_entries = []

for sc_dir in raw_root.iterdir():
    if sc_dir.is_dir():
        for run_dir in sc_dir.iterdir():
            if run_dir.is_dir() and not run_dir.name.startswith('_'):
                meta_file = run_dir / 'metadata.json'
                report_file = run_dir / 'validation_report.json'
                if meta_file.exists() and report_file.exists():
                    with meta_file.open('r', encoding='utf-8') as f:
                        meta = json.load(f)
                    with report_file.open('r', encoding='utf-8') as f:
                        report = json.load(f)
                    index_entries.append({
                        'scenario': meta.get('scenario_name'),
                        'run_id': meta.get('run_id'),
                        'started_at': meta.get('started_at'),
                        'completed_at': meta.get('completed_at'),
                        'sample_count': report.get('sample_count'),
                        'pod_sample_count': report.get('pod_sample_count'),
                        'validation_status': 'valid' if report.get('valid') else 'invalid',
                        'relative_path': str(run_dir.relative_to(raw_root)).replace('\\', '/')
                    })

index_path = raw_root / 'dataset-index.json'
with index_path.open('w', encoding='utf-8') as f:
    json.dump({
        'generated_at': datetime.now().isoformat(),
        'datasets_count': len(index_entries),
        'datasets': sorted(index_entries, key=lambda x: x.get('started_at') or '')
    }, f, indent=2)

print(f"Wrote index with {len(index_entries)} datasets to {index_path}")
'@

$pyIndexScript | .venv\Scripts\python.exe -

Write-Host "`n[COMPLETED] Batch capture and indexing finished." -ForegroundColor Green
