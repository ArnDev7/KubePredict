# Phase 7B Single Scenario Capture Script
param (
    [Parameter(Mandatory = $true)]
    [ValidateSet("steady", "ramp", "periodic", "sudden_increase", "drift")]
    [string]$Scenario,

    [int]$WorkloadPort = 18000,
    [int]$PrometheusPort = 19090,
    [string]$RunId = ""
)

$ErrorActionPreference = "Stop"

Write-Host "=== KubePredict Scenario Capture: $Scenario ===" -ForegroundColor Cyan

# 1. Verify exact context
$curCtx = (kubectl config current-context).Trim()
if ($curCtx -ne "kind-kubepredict-dev") {
    Write-Error "Invalid Kubernetes context: $curCtx. Must be kind-kubepredict-dev."
    exit 1
}

# 2. Verify fixed 2-replica state
$readyPods = (kubectl get pods -n kubepredict-system -l app.kubernetes.io/component=workload-api --context kind-kubepredict-dev --no-headers | Where-Object { $_ -match "Running" }).Count
if ($readyPods -ne 2) {
    Write-Error "Expected exactly 2 Running workload pods, found $readyPods."
    exit 1
}

# 3. Verify no live HPA exists
$hpa = kubectl get hpa -A --context kind-kubepredict-dev --ignore-not-found=true
if ($hpa) {
    Write-Error "Live HPA detected! Autoscaling is strictly forbidden in Phase 7B."
    exit 1
}

# 4. Construct execution arguments
$argsList = @(
    "-m", "experiments.telemetry.run_scenario_capture",
    "--scenario", $Scenario,
    "--workload-port", $WorkloadPort,
    "--prometheus-port", $PrometheusPort
)

if ($RunId -ne "") {
    $argsList += @("--run-id", $RunId)
}

Write-Host "Launching orchestrator for scenario '$Scenario'..." -ForegroundColor Yellow
& ".venv\Scripts\python.exe" @argsList
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Error "Scenario capture for '$Scenario' failed with exit code $exitCode!"
    exit $exitCode
}

# 5. Post-run verification
Write-Host "`nVerifying port release and post-run health..." -ForegroundColor Yellow
$connW = Get-NetTCPConnection -LocalPort $WorkloadPort -ErrorAction SilentlyContinue
$connP = Get-NetTCPConnection -LocalPort $PrometheusPort -ErrorAction SilentlyContinue

if ($connW -or $connP) {
    Write-Warning "Port $WorkloadPort or $PrometheusPort is still active!"
} else {
    Write-Host "Local ports $WorkloadPort and $PrometheusPort are cleanly released." -ForegroundColor Green
}

Write-Host "`n[COMPLETED] Scenario '$Scenario' capture finished successfully." -ForegroundColor Green
