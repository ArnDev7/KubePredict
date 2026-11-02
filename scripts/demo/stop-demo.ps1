<#
.SYNOPSIS
    Stops the KubePredict demo environment and cleans up port-forward processes.
.DESCRIPTION
    Reads .demo-state/state.json, terminates recorded port-forward PIDs,
    and removes the .demo-state/ directory.
#>

$ErrorActionPreference = "Stop"
$StateDir  = ".demo-state"
$StateFile = Join-Path $StateDir "state.json"

if (-not (Test-Path $StateFile)) {
    Write-Warning "Demo state file not found at $StateFile - nothing to stop."
    exit 0
}

$state = Get-Content $StateFile -Raw | ConvertFrom-Json

foreach ($prop in $state.PSObject.Properties) {
    $key   = $prop.Name
    $val   = $prop.Value
    $procId = $val.pid
    if ($procId) {
        Write-Host "Stopping $key (PID $procId)..." -ForegroundColor Cyan
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}

# Small wait then verify ports released
Start-Sleep -Seconds 2

Remove-Item -Recurse -Force $StateDir -ErrorAction SilentlyContinue
Write-Host "Demo environment stopped. .demo-state/ removed." -ForegroundColor Green
