<#
.SYNOPSIS
    Inspects and summarizes a Phase 9B dry-run controller run.

.DESCRIPTION
    Validates output evidence files in the specified dry-run directory,
    verifies that action_executed is always false across all recommendations,
    and displays forecast counts, recommendation counts, directions, reasons,
    and applied constraints.
    Does not rank controllers, modify evidence, or execute scaling.

.PARAMETER RunDir
    Path to the controller run directory (e.g. experiments/output/controller-dry-run/dry-run-YYYYMMDD_HHMMSS).
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
Write-Host "KubePredict Phase 9B: Inspect Dry-Run Controller Run" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Run Directory: $pRunDir"
Write-Host ""

# 1. Verify required files
$requiredFiles = @(
    "metadata.json",
    "cycles.jsonl",
    "forecasts.csv",
    "recommendations.csv",
    "matched_residuals.csv",
    "skipped_cycles.csv",
    "capacity_configuration.yaml",
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
    Write-Error "One or more required run artifacts are missing from $pRunDir."
}

# 2. Parse and display metadata
$meta = Get-Content (Join-Path $pRunDir "metadata.json") -Raw | ConvertFrom-Json
Write-Host "`n--- Run Summary ---"
Write-Host "Run ID:             $($meta.run_id)"
Write-Host "Status:             $($meta.status)"
Write-Host "Mode:               $($meta.mode)"
Write-Host "Model:              $($meta.model_name)"
Write-Host "Horizon:            $($meta.default_horizon_steps) step(s) ($($meta.default_horizon_seconds)s)"
Write-Host "Cycles Executed:    $($meta.cycles_executed) / $($meta.maximum_cycles)"
Write-Host "Forecasts Created:  $($meta.forecasts_created)"
Write-Host "Forecasts Matched:  $($meta.forecasts_matched)"
Write-Host "Recommendations:    $($meta.recommendations_created)"
Write-Host "Skipped Cycles:     $($meta.skipped_cycles_count)"
Write-Host "Effective Capacity: $($meta.effective_capacity_per_replica) req/s per replica"

# 3. Inspect recommendations and verify action_executed is strictly false
$recs = Import-Csv (Join-Path $pRunDir "recommendations.csv") -ErrorAction SilentlyContinue
if ($recs -and $recs.Count -gt 0) {
    Write-Host "`n--- Recommendations ($($recs.Count) records) ---"
    $recs | Format-Table cycle_number, predicted_request_rate_per_second, current_replicas, proposed_replicas, final_replicas, direction, reason, applied_constraints, action_executed -AutoSize

    $anyExecuted = $recs | Where-Object { $_.action_executed -ne "False" -and $_.action_executed -ne "false" }
    if ($anyExecuted) {
        Write-Error "CRITICAL SAFETY VIOLATION: action_executed is True in recommendations.csv!"
    } else {
        Write-Host "[VERIFIED] action_executed is False across all $($recs.Count) recommendation rows." -ForegroundColor Green
    }

    # Summary by direction and reason
    Write-Host "`nRecommendation Direction Counts:"
    $recs | Group-Object direction | ForEach-Object { Write-Host "  $($_.Name): $($_.Count)" }
    Write-Host "Recommendation Reason Counts:"
    $recs | Group-Object reason | ForEach-Object { Write-Host "  $($_.Name): $($_.Count)" }
} else {
    Write-Host "`nNo recommendations generated in this run." -ForegroundColor Yellow
}

# 4. Residual Summary if matched
$residuals = Import-Csv (Join-Path $pRunDir "matched_residuals.csv") -ErrorAction SilentlyContinue
if ($residuals -and $residuals.Count -gt 0) {
    Write-Host "`n--- Matched Residuals ($($residuals.Count) samples) ---"
    $residuals | Format-Table forecast_id, target_timestamp, predicted_request_rate_per_second, actual_request_rate_per_second, residual, absolute_error -AutoSize
} else {
    Write-Host "`nNo matched residuals yet (target timestamps may lie beyond bounded execution window)." -ForegroundColor Yellow
}

# 5. Skipped cycles if any
$skipped = Import-Csv (Join-Path $pRunDir "skipped_cycles.csv") -ErrorAction SilentlyContinue
if ($skipped -and $skipped.Count -gt 0) {
    Write-Host "`n--- Skipped Cycles ($($skipped.Count) cycles) ---"
    $skipped | Format-Table cycle_number, timestamp, outcome, skip_reason -AutoSize
}

Write-Host "`n[SUCCESS] Dry-run inspection completed successfully." -ForegroundColor Green
