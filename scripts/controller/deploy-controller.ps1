<#
.SYNOPSIS
    Deploys the KubePredict safe controller in committed dry-run mode (Phase 11).

.DESCRIPTION
    Validates cluster context, workload health, Prometheus availability, and HPA absence.
    Applies kubernetes/controller manifests using kubectl apply -k.
    Waits for controller Deployment rollout and verifies that no scale writes or workload
    mutations occur.
#>
[CmdletBinding()]
param (
    [string]$Context = "kind-kubepredict-dev",
    [string]$Namespace = "kubepredict-system",
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

Write-Host "=== Deploying KubePredict Safe Controller (Dry-Run Mode) ===" -ForegroundColor Cyan

# 1. Verify exact context
$currentContext = kubectl config current-context
if ($currentContext -ne $Context) {
    Write-Error "Invalid context: Expected '$Context', got '$currentContext'."
}
Write-Host "[PASS] Context verified: $Context" -ForegroundColor Green

# 2. Verify no HPA
Write-Host "Checking for existing HorizontalPodAutoscalers..."
try { $hpaList = kubectl --context $Context get hpa -A --no-headers 2>$null } catch { $hpaList = $null }
if ($hpaList) {
    Write-Error "Safety violation: Live HPA detected in cluster. Refusing to deploy controller."
}
Write-Host "[PASS] No live HPA detected" -ForegroundColor Green

# 3. Verify workload health
Write-Host "Verifying workload deployment status..."
$readyReplicas = kubectl --context $Context get deployment kubepredict-workload-api -n $Namespace -o jsonpath='{.status.readyReplicas}'
if ($readyReplicas -ne "2") {
    Write-Error "Workload must have exactly 2 Ready replicas before controller deployment. Observed: '$readyReplicas'"
}
Write-Host "[PASS] Workload has exactly 2 Ready replicas" -ForegroundColor Green

# 4. Verify Prometheus health
Write-Host "Verifying Prometheus monitoring health..."
$promReady = kubectl --context $Context get deployment kubepredict-prometheus -n kubepredict-monitoring -o jsonpath='{.status.readyReplicas}' 2>$null
if ($promReady -ne "1") {
    Write-Error "Prometheus deployment is not ready in kubepredict-monitoring. Observed: '$promReady'"
}
Write-Host "[PASS] Prometheus is healthy" -ForegroundColor Green

# 5. Apply controller manifests (defaults to dry_run and ENABLE_LIVE_SCALING=false)
Write-Host "`nApplying controller manifests in namespace '$Namespace'..."
$manifestPath = Join-Path $RepoRoot "kubernetes\controller"
kubectl --context $Context apply -k $manifestPath
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to apply controller manifests"
}

# 6. Wait for rollout
Write-Host "`nWaiting for controller Deployment rollout ($TimeoutSeconds s timeout)..."
kubectl --context $Context rollout status deployment/kubepredict-controller -n $Namespace --timeout="${TimeoutSeconds}s"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Controller Deployment rollout failed or timed out"
}

# 7. Verify controller pod and safe defaults
Start-Sleep -Seconds 3
$cmMode = kubectl --context $Context get configmap kubepredict-controller-config -n $Namespace -o jsonpath='{.data.CONTROLLER_MODE}'
$cmLive = kubectl --context $Context get configmap kubepredict-controller-config -n $Namespace -o jsonpath='{.data.ENABLE_LIVE_SCALING}'
if ($cmMode -ne "dry_run" -or $cmLive -ne "false") {
    Write-Error "Safety check failed: Committed configmap must be dry_run and false. Got mode='$cmMode', live='$cmLive'"
}
Write-Host "[PASS] Controller ConfigMap committed defaults verified: CONTROLLER_MODE=$cmMode, ENABLE_LIVE_SCALING=$cmLive" -ForegroundColor Green

# 8. Verify workload replicas unchanged
$currentReplicas = kubectl --context $Context get deployment kubepredict-workload-api -n $Namespace -o jsonpath='{.spec.replicas}'
if ($currentReplicas -ne "2") {
    Write-Error "Workload replicas changed unexpectedly: Expected 2, got $currentReplicas"
}
Write-Host "[PASS] Workload replicas unchanged at fixed baseline (2)" -ForegroundColor Green

Write-Host "`n=== Controller Dry-Run Deployment Complete and Verified ===" -ForegroundColor Green

