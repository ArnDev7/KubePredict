<#
.SYNOPSIS
    Inspects an existing Phase 8A offline forecasting evaluation output directory.

.DESCRIPTION
    Validates output completeness, displays per-horizon candidate models, provisional selection,
    and validation rankings. Does not recompute results or alter files.

.PARAMETER EvaluationDir
    Path to evaluation directory under data/processed/forecasting/<eval_id>.
#>
[CmdletBinding()]
param (
    [Parameter(Mandatory = $true)]
    [string]$EvaluationDir
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment Python executable not found at: $PythonExe"
}

if (-not (Test-Path $EvaluationDir)) {
    Write-Error "Evaluation directory does not exist: $EvaluationDir"
}

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "KubePredict Phase 8A: Inspecting Forecasting Evaluation Results" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Evaluation Directory: $EvaluationDir"
Write-Host ""

& $PythonExe -m forecasting.cli inspect --evaluation-dir $EvaluationDir
if ($LASTEXITCODE -ne 0) {
    Write-Error "Forecasting inspection failed with exit code $LASTEXITCODE"
}
