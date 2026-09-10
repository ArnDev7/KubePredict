# Phase 7B Telemetry Plan Validator (Offline)
$ErrorActionPreference = "Stop"

Write-Host "=== Validating KubePredict Telemetry Capture Plan (Offline) ===" -ForegroundColor Cyan

# 1. Validate scenario profiles
Write-Host "`n1. Validating all 5 scenario definitions..." -ForegroundColor Yellow
& ".\scripts\load-testing\validate-scenarios.ps1"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Scenario validation failed!"
    exit 1
}

# 2. Validate telemetry query specification
Write-Host "`n2. Validating telemetry query specification..." -ForegroundColor Yellow
$pyScript = @'
from experiments.telemetry.query_spec import load_query_spec, compute_query_spec_hash
cfg = load_query_spec('experiments/configs/telemetry_queries.yaml')
h = compute_query_spec_hash('experiments/configs/telemetry_queries.yaml')
print(f"Query spec loaded successfully: {len(cfg.queries)} system queries, {len(cfg.pod_queries)} pod queries. Hash: {h[:16]}...")
'@

$pyScript | .venv\Scripts\python.exe -
if ($LASTEXITCODE -ne 0) {
    Write-Error "Query specification validation failed!"
    exit 1
}

# 3. Validate git ignore rule for raw datasets
Write-Host "`n3. Verifying output directory Git ignore rules..." -ForegroundColor Yellow
$sampleTestFile = "data/raw/scenarios/steady/test-run/telemetry.csv"
$ignoreCheck = git check-ignore -v $sampleTestFile
if ($LASTEXITCODE -eq 0 -and $ignoreCheck) {
    Write-Host "Git ignore confirmed for: $sampleTestFile" -ForegroundColor Green
    Write-Host "Rule: $ignoreCheck"
} else {
    Write-Error "Output path $sampleTestFile is NOT properly ignored by Git!"
    exit 1
}

Write-Host "`n[SUCCESS] Telemetry plan and query specification verified successfully." -ForegroundColor Green
