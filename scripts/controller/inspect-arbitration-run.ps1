<#
.SYNOPSIS
    Inspects and validates a Phase 10B Arbitrated Dry-Run controller run.

.DESCRIPTION
    Validates output evidence files in the specified arbitration run directory,
    verifies that action_executed is strictly false across all decision records,
    and displays policy mode breakdown, reason counts, rapid-change count,
    predictive/reactive recommendation counts, and cooldown/stabilization counts.
    Does not rank controllers, claim performance improvement, or execute scaling.

.PARAMETER RunDir
    Path to the controller run directory (e.g. experiments/output/controller-arbitration/arbitration-dry-run-YYYYMMDD_HHMMSS).
#>
[CmdletBinding()]
param (
    [Parameter(Mandatory=$true)]
    [string]$RunDir
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

$pRunDir = Resolve-Path $RunDir -ErrorAction SilentlyContinue
if (-not $pRunDir -or -not (Test-Path $pRunDir)) {
    Write-Error "Run directory not found at: $RunDir"
}

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "KubePredict Phase 10B: Inspect Arbitrated Dry-Run Execution" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Run Directory: $pRunDir"
Write-Host ""

# 1. Verify required files
$requiredFiles = @(
    "metadata.json",
    "cycles.jsonl",
    "forecasts.csv",
    "trust_decisions.csv",
    "rapid_change_decisions.csv",
    "reactive_recommendations.csv",
    "arbitration_decisions.csv",
    "matched_residuals.csv",
    "skipped_cycles.csv",
    "policy_configuration.yaml",
    "validation_report.json",
    "execution.log"
)

$allPresent = $true
foreach ($rf in $requiredFiles) {
    $fPath = Join-Path $pRunDir $rf
    if (Test-Path $fPath) {
        Write-Host "  [OK] $rf" -ForegroundColor Green
    } else {
        Write-Host "  [MISSING] $rf" -ForegroundColor Red
        $allPresent = $false
    }
}

if (-not $allPresent) {
    Write-Error "One or more required Phase 10B evidence files are missing."
}

# 2. Parse metadata.json
$metaPath = Join-Path $pRunDir "metadata.json"
$meta = Get-Content $metaPath -Raw | ConvertFrom-Json

Write-Host "`n--- RUN METADATA ---" -ForegroundColor Yellow
Write-Host "  Run ID:              $($meta.run_id)"
Write-Host "  Phase:               $($meta.phase)"
Write-Host "  Mode:                $($meta.mode)"
Write-Host "  Started:             $($meta.started_at)"
Write-Host "  Completed:           $($meta.completed_at)"
Write-Host "  Duration:            $($meta.duration_seconds)s"
Write-Host "  Status:              $($meta.status)"
Write-Host "  Cycles:              $($meta.cycles_completed) / $($meta.cycles_requested)"
Write-Host "  Forecasts:           $($meta.forecasts_generated)"
Write-Host "  Matched Residuals:   $($meta.forecasts_matched)"
Write-Host "  Action Executed All False: $($meta.action_executed_all_false)"

Write-Host "`n--- POLICY MODE COUNTS ---" -ForegroundColor Yellow
foreach ($prop in $meta.mode_counts.PSObject.Properties) {
    Write-Host ("  {0,-35} : {1}" -f $prop.Name, $prop.Value)
}

Write-Host "`n--- DECISION REASON COUNTS ---" -ForegroundColor Yellow
foreach ($prop in $meta.reason_counts.PSObject.Properties) {
    Write-Host ("  {0,-35} : {1}" -f $prop.Name, $prop.Value)
}

Write-Host "`n--- DAMPENING & RAPID CHANGE COUNTS ---" -ForegroundColor Yellow
Write-Host "  Rapid Change Detected Count: $($meta.rapid_change_detected_count)"
Write-Host "  Cooldown Blocked Count:       $($meta.cooldown_blocked_count)"
Write-Host "  Downscale Stabilized Count:   $($meta.downscale_stabilized_count)"

# 3. Audit arbitration_decisions.csv
$decPath = Join-Path $pRunDir "arbitration_decisions.csv"
$decisions = Import-Csv $decPath

$hasActionTrue = $false
$totalDecs = 0
foreach ($d in $decisions) {
    $totalDecs++
    if ($d.action_executed -eq "True" -or $d.action_executed -eq "true") {
        $hasActionTrue = $true
    }
}

Write-Host "`n--- SAFETY AUDIT: ACTION_EXECUTED ---" -ForegroundColor Yellow
if ($hasActionTrue) {
    Write-Host "  [FAIL] Detected action_executed=True in arbitration_decisions.csv!" -ForegroundColor Red
    exit 1
} else {
    Write-Host "  [PASS] action_executed is strictly FALSE across all $totalDecs decision records." -ForegroundColor Green
}

# 4. Parse validation_report.json
$valPath = Join-Path $pRunDir "validation_report.json"
$valReport = Get-Content $valPath -Raw | ConvertFrom-Json
if (-not $valReport.passed) {
    Write-Host "`n[FAIL] validation_report.json reported passed=False" -ForegroundColor Red
    exit 1
}

Write-Host "`n[SUCCESS] Inspection completed successfully." -ForegroundColor Green
