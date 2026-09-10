$ErrorActionPreference = "Stop"

$PythonExe = ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Error "Project Python virtual environment interpreter not found at '$PythonExe'."
}

$Scenarios = @("steady", "ramp", "periodic", "sudden_increase", "drift")

Write-Host "=== Validating KubePredict Request Scenarios (Dry-Run Mode) ==="
Write-Host "Verifying all $($Scenarios.Count) committed scenario definitions...`n"

foreach ($sc in $Scenarios) {
    Write-Host "Validating scenario '$sc'..."
    & $PythonExe -m load_testing.runner --scenario $sc --dry-run
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Dry-run validation failed for scenario '$sc' (exit code $LASTEXITCODE)."
    }
    Write-Host ""
}

Write-Host "All $($Scenarios.Count) request scenarios validated successfully in dry-run mode."
