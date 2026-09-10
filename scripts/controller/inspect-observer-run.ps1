<#
.SYNOPSIS
    Inspects and summarizes a Phase 9A observe-only controller run.

.DESCRIPTION
    Validates output evidence files in the specified run directory and displays
    cycle outcomes, forecast counts, matched actuals, and residual errors.
    Does not rank models, modify evidence, or calculate replicas.

.PARAMETER RunDir
    Path to the controller run directory (e.g. experiments/output/controller-observe/observe-YYYYMMDD_HHMMSS).
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
Write-Host "KubePredict Phase 9A: Inspect Observer Run" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Run Directory: $pRunDir"
Write-Host ""

# Verify required files
$requiredFiles = @(
    "metadata.json",
    "cycles.jsonl",
    "forecasts.csv",
    "matched_residuals.csv",
    "skipped_cycles.csv",
    "validation_report.json"
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

# Parse and display metadata
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
Write-Host "Forecasts Pending:  $($meta.forecasts_pending)"
Write-Host "Forecasts Expired:  $($meta.forecasts_expired)"

# Residual Summary if matched
$residuals = Import-Csv (Join-Path $pRunDir "matched_residuals.csv") -ErrorAction SilentlyContinue
if ($residuals -and $residuals.Count -gt 0) {
    Write-Host "`n--- Matched Residuals ($($residuals.Count) samples) ---"
    $residuals | Format-Table forecast_id, target_timestamp, predicted_request_rate_per_second, actual_request_rate_per_second, residual, absolute_error -AutoSize
} else {
    Write-Host "`nNo matched residuals yet (target timestamps may lie beyond bounded execution window)." -ForegroundColor Yellow
}

# Skipped cycles if any
$skipped = Import-Csv (Join-Path $pRunDir "skipped_cycles.csv") -ErrorAction SilentlyContinue
if ($skipped -and $skipped.Count -gt 0) {
    Write-Host "`n--- Skipped Cycles ($($skipped.Count) cycles) ---"
    $skipped | Format-Table cycle_number, timestamp, outcome, skip_reason -AutoSize
}

Write-Host "`n[SUCCESS] Inspection completed." -ForegroundColor Green
