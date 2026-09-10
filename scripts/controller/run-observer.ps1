<#
.SYNOPSIS
    Runs bounded observe-only controller against local Prometheus (Phase 9A).

.DESCRIPTION
    Verifies fixed 2-replica baseline and Prometheus readiness, starts a temporary
    Prometheus port-forward, runs bounded observer cycles, captures operational metrics
    and residual evidence, and stops all background processes in a finally block.
    Makes zero Kubernetes API write calls and zero scaling recommendations.

.PARAMETER MaximumCycles
    Number of observation cycles to execute. Defaults to 12.

.PARAMETER ConfigPath
    Path to controller YAML configuration. Defaults to config/observer.yaml.

.PARAMETER FrozenForecasterPath
    Path to frozen forecaster YAML configuration. Defaults to forecasting/config/final_forecaster.yaml.

.PARAMETER OutputRoot
    Root directory for generated runtime artifacts. Defaults to experiments/output/controller-observe.
#>
[CmdletBinding()]
param (
    [int]$MaximumCycles = 12,
    [string]$ConfigPath = "config/observer.yaml",
    [string]$FrozenForecasterPath = "forecasting/config/final_forecaster.yaml",
    [string]$OutputRoot = "experiments/output/controller-observe"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

$ExpectedContext = "kind-kubepredict-dev"
$MonitoringNamespace = "kubepredict-monitoring"
$WorkloadNamespace = "kubepredict-system"
$PrometheusService = "kubepredict-prometheus"
$PrometheusPort = 9090
$MetricsPort = 9101

$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment Python executable not found at: $PythonExe"
}

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "KubePredict Phase 9A: Run Bounded Observe-Only Controller" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Context:        $ExpectedContext"
Write-Host "Maximum Cycles: $MaximumCycles"
Write-Host "Config Path:    $ConfigPath"
Write-Host "Output Root:    $OutputRoot"
Write-Host "Mode:           OBSERVE-ONLY (Zero K8s mutations, Zero replica logic)"
Write-Host ""

# 1. Read-only Kubernetes Environment Verification
$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context is '$currentContext'; expected '$ExpectedContext'."
}

# Verify fixed workload replicas
$deployJson = kubectl get deployment kubepredict-workload-api -n $WorkloadNamespace -o json | ConvertFrom-Json
$desired = $deployJson.spec.replicas
$available = $deployJson.status.availableReplicas
Write-Host "[CHECK] Workload Deployment: desired=$desired, available=$available"
if ($desired -ne 2 -or $available -ne 2) {
    Write-Error "Workload replicas not in fixed 2-replica state (desired=$desired, available=$available)."
}

# Verify no HPA exists
$hpaList = (kubectl get hpa -A --no-headers 2>&1)
if ($hpaList -and $hpaList -notmatch "No resources found") {
    Write-Error "Active HPA found in cluster. Live HPA is forbidden in Phase 9A."
}
Write-Host "[CHECK] Zero active HPA resources verified."

# Verify Prometheus is Ready
$promDeploy = kubectl get deployment $PrometheusService -n $MonitoringNamespace -o json | ConvertFrom-Json
if ($promDeploy.status.availableReplicas -lt 1) {
    Write-Error "Prometheus deployment is not available in $MonitoringNamespace."
}
Write-Host "[CHECK] Prometheus deployment is available."

# Verify local ports 9090 and 9101 are free
$p9090 = Get-NetTCPConnection -LocalPort $PrometheusPort -ErrorAction SilentlyContinue
if ($p9090) {
    Write-Error "Port $PrometheusPort is already in use. Clean up existing process first."
}
$p9101 = Get-NetTCPConnection -LocalPort $MetricsPort -ErrorAction SilentlyContinue
if ($p9101) {
    Write-Error "Port $MetricsPort is already in use. Clean up existing process first."
}
Write-Host "[CHECK] Local ports $PrometheusPort and $MetricsPort are free."

# 2. Start temporary Prometheus port-forward with captured PID
Write-Host "`nStarting temporary Prometheus port-forward (PID captured)..." -ForegroundColor Yellow
$promPf = Start-Process -FilePath "kubectl" -ArgumentList "port-forward service/$PrometheusService ${PrometheusPort}:9090 -n $MonitoringNamespace --context $ExpectedContext" -PassThru -NoNewWindow
$promPid = $promPf.Id
Write-Host "Prometheus port-forward started (PID: $promPid)."

try {
    # Wait for port-forward readiness
    $ready = $false
    for ($i = 1; $i -le 10; $i++) {
        Start-Sleep -Seconds 1
        try {
            $h = (Invoke-WebRequest -Uri "http://127.0.0.1:${PrometheusPort}/-/healthy" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop).Content
            if ($h -match "Healthy") {
                $ready = $true
                break
            }
        } catch {}
    }
    if (-not $ready) {
        Write-Error "Prometheus port-forward on port $PrometheusPort did not become healthy."
    }
    Write-Host "[OK] Prometheus port-forward is healthy.`n"

    # 3. Execute bounded observer cycles
    Write-Host "Executing $MaximumCycles observer cycles..." -ForegroundColor Cyan
    & $PythonExe -m controller.cli observe --config $ConfigPath --frozen-forecaster $FrozenForecasterPath --output-root $OutputRoot --max-cycles $MaximumCycles

    if ($LASTEXITCODE -ne 0) {
        Write-Error "Observer controller failed with exit code $LASTEXITCODE"
    }

} finally {
    # 4. Strictly guaranteed process cleanup
    Write-Host "`nStopping Prometheus port-forward (PID: $promPid)..." -ForegroundColor Yellow
    Stop-Process -Id $promPid -Force -ErrorAction SilentlyContinue

    # Verify ports released
    Start-Sleep -Seconds 1
    $rem9090 = Get-NetTCPConnection -LocalPort $PrometheusPort -ErrorAction SilentlyContinue
    $rem9101 = Get-NetTCPConnection -LocalPort $MetricsPort -ErrorAction SilentlyContinue
    if ($rem9090) {
        Write-Warning "Port $PrometheusPort was not cleanly released."
    } else {
        Write-Host "[OK] Port $PrometheusPort released."
    }
    if ($rem9101) {
        Write-Warning "Port $MetricsPort was not cleanly released."
    } else {
        Write-Host "[OK] Port $MetricsPort released."
    }
}

Write-Host "`n[SUCCESS] Phase 9A bounded observer run completed cleanly." -ForegroundColor Green
