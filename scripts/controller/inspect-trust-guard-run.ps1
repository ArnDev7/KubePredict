<#
.SYNOPSIS
    Inspects and validates a Phase 10A Forecast Trust Guard run.

.DESCRIPTION
    Validates output evidence files in the specified trust guard directory,
    verifies that action_executed is strictly false across all records,
    and displays trust decision counts, outcome breakdown, error metrics,
    threshold values, and recommendation eligibility.
    Does not rank controllers, modify evidence, or execute scaling.

.PARAMETER RunDir
    Path to the controller run directory (e.g. experiments/output/controller-trust-guard/guarded-dry-run-YYYYMMDD_HHMMSS).
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
Write-Host "KubePredict Phase 10A: Inspect Forecast Trust Guard Run" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Run Directory: $pRunDir"
Write-Host ""

# 1. Verify required files
$requiredFiles = @(
    "metadata.json",
    "cycles.jsonl",
    "forecasts.csv",
    "trust_decisions.csv",
    "matched_residuals.csv",
    "guarded_recommendations.csv",
    "skipped_cycles.csv",
    "trust_guard_configuration.yaml",
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
    Write-Error "One or more required evidence files are missing from: $pRunDir"
}

# 2. Inspect metadata.json
Write-Host "`n--- RUN METADATA ---" -ForegroundColor Yellow
$meta = Get-Content (Join-Path $pRunDir "metadata.json") -Raw | ConvertFrom-Json
Write-Host "  Run ID:              $($meta.run_id)"
Write-Host "  Phase:               $($meta.phase)"
Write-Host "  Mode:                $($meta.mode)"
Write-Host "  Started:             $($meta.started_at)"
Write-Host "  Completed:           $($meta.completed_at)"
Write-Host "  Duration:            $($meta.duration_seconds)s"
Write-Host "  Status:              $($meta.status)"
Write-Host "  Cycles:              $($meta.cycles_completed) / $($meta.cycles_requested)"
Write-Host "  Trusted:             $($meta.trusted_count)"
Write-Host "  Untrusted:           $($meta.untrusted_count)"
Write-Host "  Eligible Recs:       $($meta.recommendation_eligible_count)"
Write-Host "  Blocked Recs:        $($meta.recommendation_blocked_count)"
Write-Host "  Forecasts:           $($meta.forecasts_generated)"
Write-Host "  Matched Residuals:   $($meta.forecasts_matched)"
Write-Host "  Absolute Threshold:  $($meta.absolute_error_threshold) req/s"
Write-Host "  Relative Threshold:  $($meta.relative_error_threshold)% sMAPE"
Write-Host "  Action Executed All False: $($meta.action_executed_all_false)"

# 3. Inspect Trust Decisions Breakdown
Write-Host "`n--- TRUST DECISIONS BREAKDOWN ---" -ForegroundColor Yellow
$decisions = Import-Csv (Join-Path $pRunDir "trust_decisions.csv")
$decisions | Group-Object outcome | ForEach-Object {
    Write-Host ("  {0,-35} : {1}" -f $_.Name, $_.Count)
}

# 4. Strict action_executed check
Write-Host "`n--- SAFETY AUDIT: ACTION_EXECUTED ---" -ForegroundColor Yellow
$anyActionExecuted = $false
foreach ($d in $decisions) {
    if ($d.action_executed -ne "False" -and $d.action_executed -ne "false") {
        Write-Host "  [VIOLATION] Cycle $($d.cycle_number) has action_executed=$($d.action_executed)!" -ForegroundColor Red
        $anyActionExecuted = $true
    }
}
$recs = Import-Csv (Join-Path $pRunDir "guarded_recommendations.csv")
foreach ($r in $recs) {
    if ($r.action_executed -ne "False" -and $r.action_executed -ne "false") {
        Write-Host "  [VIOLATION] Recommendation cycle $($r.cycle_number) has action_executed=$($r.action_executed)!" -ForegroundColor Red
        $anyActionExecuted = $true
    }
}

if ($anyActionExecuted) {
    Write-Error "CRITICAL AUDIT FAILURE: action_executed was True in one or more records!"
} else {
    Write-Host "  [PASS] action_executed is strictly FALSE across all $($decisions.Count) trust decisions and $($recs.Count) recommendations." -ForegroundColor Green
}

# 5. Residual evidence summary
Write-Host "`n--- RESIDUAL EVIDENCE SUMMARY ---" -ForegroundColor Yellow
$residuals = Import-Csv (Join-Path $pRunDir "matched_residuals.csv")
Write-Host "  Total Matched Residuals: $($residuals.Count)"
if ($residuals.Count -gt 0) {
    $absErrors = $residuals | ForEach-Object { [double]$_.absolute_error }
    $meanAbsError = ($absErrors | Measure-Object -Average).Average
    Write-Host ("  Mean Absolute Error:     {0:N4} req/s" -f $meanAbsError)
}

Write-Host "`n[SUCCESS] Inspection completed successfully." -ForegroundColor Green
