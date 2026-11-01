<#
.SYNOPSIS
    Runs bounded arbitrated dry-run controller with rapid-change detection & fallback (Phase 10B).

.DESCRIPTION
    Verifies fixed 2-replica baseline, Metrics Server, and Prometheus readiness.
    Starts a temporary Prometheus port-forward, runs bounded arbitrated dry-run cycles,
    evaluates predictive, rapid-change override, and reactive fallback policies with
    bounds, step limits, downscale stabilization, and cooldown dampening.
    Stops all background processes in a finally block.
    Guarantees action_executed=False, zero Kubernetes writes, and zero live mutations.

.PARAMETER MaximumCycles
    Number of arbitrated dry-run cycles to execute. Defaults to 16.

.PARAMETER ConfigPath
    Path to controller YAML configuration. Defaults to config/observer.yaml.

.PARAMETER CapacityConfigPath
    Path to capacity model YAML configuration. Defaults to controller/config/capacity.yaml.

.PARAMETER TrustGuardConfigPath
    Path to trust guard YAML configuration. Defaults to controller/config/trust_guard.yaml.

.PARAMETER PolicyConfigPath
    Path to policy YAML configuration. Defaults to controller/config/policy.yaml.

.PARAMETER FrozenForecasterPath
    Path to frozen forecaster YAML configuration. Defaults to forecasting/config/final_forecaster.yaml.

.PARAMETER OutputRoot
    Root directory for generated runtime artifacts. Defaults to experiments/output/controller-arbitration.

.PARAMETER RunId
    Optional explicit run identifier.
#>
[CmdletBinding()]
param (
    [int]$MaximumCycles = 16,
    [string]$ConfigPath = "config/observer.yaml",
    [string]$CapacityConfigPath = "controller/config/capacity.yaml",
    [string]$TrustGuardConfigPath = "controller/config/trust_guard.yaml",
    [string]$PolicyConfigPath = "controller/config/policy.yaml",
    [string]$FrozenForecasterPath = "forecasting/config/final_forecaster.yaml",
    [string]$OutputRoot = "experiments/output/controller-arbitration",
    [string]$RunId = ""
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
Write-Host "KubePredict Phase 10B: Run Bounded Arbitrated Dry-Run Controller" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Context:            $ExpectedContext"
Write-Host "Maximum Cycles:     $MaximumCycles"
Write-Host "Config Path:        $ConfigPath"
Write-Host "Capacity Config:    $CapacityConfigPath"
Write-Host "Trust Guard Config: $TrustGuardConfigPath"
Write-Host "Policy Config:      $PolicyConfigPath"
Write-Host "Output Root:        $OutputRoot"
Write-Host "Mode:               ARBITRATED DRY-RUN (action_executed=False, Zero K8s mutations)"
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
if ($desired -ne 2 -or $available -ne 2) {
    Write-Error "Workload replicas not in baseline state: desired=$desired, available=$available. Expected 2."
}
Write-Host "[OK] Workload baseline confirmed: 2 replicas desired, 2 available." -ForegroundColor Green

# Verify no active HPA
$hpaList = (kubectl get hpa -A --ignore-not-found)
if ($hpaList) {
    Write-Error "Active HPA found in cluster. Live HPA is forbidden in Phase 10B."
}
Write-Host "[OK] No live HPA found in cluster." -ForegroundColor Green

# Verify Metrics Server readiness
$msDeploy = kubectl get deployment metrics-server -n kube-system -o json | ConvertFrom-Json
if ($msDeploy.status.availableReplicas -lt 1) {
    Write-Error "Metrics Server deployment is not available in kube-system."
}
Write-Host "[OK] Metrics Server deployment is available." -ForegroundColor Green

# Verify Prometheus readiness
$promDeploy = kubectl get deployment $PrometheusService -n $MonitoringNamespace -o json | ConvertFrom-Json
if ($promDeploy.status.availableReplicas -lt 1) {
    Write-Error "Prometheus deployment is not available in $MonitoringNamespace."
}
Write-Host "[OK] Prometheus deployment is available." -ForegroundColor Green

# Verify local ports 9090 and 9101 are free
$p9090 = Get-NetTCPConnection -LocalPort $PrometheusPort -ErrorAction SilentlyContinue
if ($p9090) {
    Write-Error "Port $PrometheusPort is already in use. Clean up existing process first."
}
$p9101 = Get-NetTCPConnection -LocalPort $MetricsPort -ErrorAction SilentlyContinue
if ($p9101) {
    Write-Error "Port $MetricsPort is already in use. Clean up existing process first."
}
Write-Host "[OK] Local ports $PrometheusPort and $MetricsPort are free." -ForegroundColor Green

# 2. Start temporary Prometheus port-forward with captured PID
Write-Host "`nStarting temporary Prometheus port-forward (PID captured)..." -ForegroundColor Yellow
$promPf = Start-Process -FilePath "kubectl" -ArgumentList "port-forward service/$PrometheusService ${PrometheusPort}:9090 -n $MonitoringNamespace --context $ExpectedContext" -PassThru -NoNewWindow
$promPid = $promPf.Id
Write-Host "Prometheus port-forward started (PID: $promPid)."

$ready = $false
for ($i = 1; $i -le 15; $i++) {
    Start-Sleep -Seconds 1
    try {
        $h = (Invoke-WebRequest -Uri "http://127.0.0.1:${PrometheusPort}/-/healthy" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop).Content
        if ($h -match "Healthy") {
            $ready = $true
            break
        }
    } catch {
        # Retry until ready
    }
}

if (-not $ready) {
    Stop-Process -Id $promPid -Force -ErrorAction SilentlyContinue
    Write-Error "Prometheus port-forward failed to become ready on port $PrometheusPort."
}
Write-Host "[OK] Prometheus port-forward ready on http://127.0.0.1:$PrometheusPort" -ForegroundColor Green

try {
    # 3. Execute Arbitrated Dry-Run Controller CLI
    Write-Host "`nLaunching arbitrated dry-run controller loop ($MaximumCycles cycles)..." -ForegroundColor Yellow
    $cliArgs = @(
        "-m", "controller.cli", "arbitration-dry-run",
        "--config", $ConfigPath,
        "--capacity-config", $CapacityConfigPath,
        "--trust-guard-config", $TrustGuardConfigPath,
        "--policy-config", $PolicyConfigPath,
        "--frozen-forecaster", $FrozenForecasterPath,
        "--output-root", $OutputRoot,
        "--max-cycles", $MaximumCycles
    )
    if ($RunId) {
        $cliArgs += @("--run-id", $RunId)
    }

    & $PythonExe $cliArgs

    if ($LASTEXITCODE -ne 0) {
        Write-Error "Arbitrated dry-run controller exited with error code $LASTEXITCODE."
    }
    Write-Host "`n[SUCCESS] Arbitrated dry-run controller loop completed successfully." -ForegroundColor Green

} finally {
    Write-Host "`nCleaning up background jobs..." -ForegroundColor Yellow
    if ($promPid) {
        Stop-Process -Id $promPid -Force -ErrorAction SilentlyContinue
        Write-Host "[OK] Prometheus port-forward (PID: $promPid) stopped." -ForegroundColor Green
    }

    # Verify ports released
    Start-Sleep -Milliseconds 500
    $promActive = Get-NetTCPConnection -LocalPort $PrometheusPort -ErrorAction SilentlyContinue
    $metricsActive = Get-NetTCPConnection -LocalPort $MetricsPort -ErrorAction SilentlyContinue

    if ($promActive) {
        Write-Warning "Port $PrometheusPort still reported active by TCP connection table."
    } else {
        Write-Host "[OK] Prometheus port $PrometheusPort released." -ForegroundColor Green
    }

    if ($metricsActive) {
        Write-Warning "Metrics port $MetricsPort still reported active by TCP connection table."
    } else {
        Write-Host "[OK] Controller metrics port $MetricsPort released." -ForegroundColor Green
    }
}
