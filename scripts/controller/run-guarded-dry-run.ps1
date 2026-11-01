<#
.SYNOPSIS
    Runs bounded guarded dry-run controller with Forecast Trust Guard (Phase 10A).

.DESCRIPTION
    Verifies fixed 2-replica baseline and Prometheus readiness, starts a temporary
    Prometheus port-forward, runs bounded guarded dry-run cycles, captures trust decisions,
    evaluates dry-run capacity recommendations, captures evidence, and stops all background
    processes in a finally block.
    Guarantees action_executed=False, zero Kubernetes writes, and zero reactive fallback.

.PARAMETER MaximumCycles
    Number of guarded dry-run cycles to execute. Defaults to 16.

.PARAMETER ConfigPath
    Path to controller YAML configuration. Defaults to config/observer.yaml.

.PARAMETER CapacityConfigPath
    Path to capacity model YAML configuration. Defaults to controller/config/capacity.yaml.

.PARAMETER TrustGuardConfigPath
    Path to trust guard YAML configuration. Defaults to controller/config/trust_guard.yaml.

.PARAMETER FrozenForecasterPath
    Path to frozen forecaster YAML configuration. Defaults to forecasting/config/final_forecaster.yaml.

.PARAMETER OutputRoot
    Root directory for generated runtime artifacts. Defaults to experiments/output/controller-trust-guard.

.PARAMETER RunId
    Optional explicit run identifier.
#>
[CmdletBinding()]
param (
    [int]$MaximumCycles = 16,
    [string]$ConfigPath = "config/observer.yaml",
    [string]$CapacityConfigPath = "controller/config/capacity.yaml",
    [string]$TrustGuardConfigPath = "controller/config/trust_guard.yaml",
    [string]$FrozenForecasterPath = "forecasting/config/final_forecaster.yaml",
    [string]$OutputRoot = "experiments/output/controller-trust-guard",
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
Write-Host "KubePredict Phase 10A: Run Bounded Guarded Dry-Run Controller" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Context:            $ExpectedContext"
Write-Host "Maximum Cycles:     $MaximumCycles"
Write-Host "Config Path:        $ConfigPath"
Write-Host "Capacity Config:    $CapacityConfigPath"
Write-Host "Trust Guard Config: $TrustGuardConfigPath"
Write-Host "Output Root:        $OutputRoot"
Write-Host "Mode:               GUARDED DRY-RUN (action_executed=False, Zero K8s mutations)"
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
$hpaList = (kubectl get hpa -A --ignore-not-found)
if ($hpaList) {
    Write-Error "Active HPA found in cluster. Live HPA is forbidden in Phase 10A."
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
        } catch {
            # Retry
        }
    }
    if (-not $ready) {
        Write-Error "Timed out waiting for Prometheus port-forward readiness."
    }
    Write-Host "Prometheus port-forward is ready on port $PrometheusPort."

    # 3. Build controller CLI arguments
    $cliArgs = @(
        "-m", "controller.cli", "guarded-dry-run",
        "--config", $ConfigPath,
        "--capacity-config", $CapacityConfigPath,
        "--trust-guard-config", $TrustGuardConfigPath,
        "--frozen-forecaster", $FrozenForecasterPath,
        "--output-root", $OutputRoot,
        "--max-cycles", $MaximumCycles
    )
    if ($RunId) {
        $cliArgs += @("--run-id", $RunId)
    }

    Write-Host "`nExecuting guarded dry-run controller loop ($MaximumCycles cycles)..." -ForegroundColor Yellow
    & $PythonExe $cliArgs
    $controllerExitCode = $LASTEXITCODE

    if ($controllerExitCode -ne 0) {
        Write-Error "Guarded dry-run controller failed with exit code $controllerExitCode."
    }
} finally {
    Write-Host "`nCleaning up temporary port-forward (PID: $promPid)..." -ForegroundColor Yellow
    if ($promPid) {
        try {
            Stop-Process -Id $promPid -Force -ErrorAction SilentlyContinue
            Write-Host "Prometheus port-forward process (PID: $promPid) stopped."
        } catch {
            Write-Warning "Could not stop port-forward PID: $promPid."
        }
    }

    # Verify ports released
    Start-Sleep -Seconds 1
    $p9090End = Get-NetTCPConnection -LocalPort $PrometheusPort -ErrorAction SilentlyContinue
    $p9101End = Get-NetTCPConnection -LocalPort $MetricsPort -ErrorAction SilentlyContinue
    if ($p9090End) {
        Write-Warning "Port $PrometheusPort is still bound after shutdown."
    } else {
        Write-Host "Port $PrometheusPort successfully released." -ForegroundColor Green
    }
    if ($p9101End) {
        Write-Warning "Port $MetricsPort is still bound after shutdown."
    } else {
        Write-Host "Port $MetricsPort successfully released." -ForegroundColor Green
    }
}

Write-Host "`n[SUCCESS] Guarded dry-run controller execution complete." -ForegroundColor Green
