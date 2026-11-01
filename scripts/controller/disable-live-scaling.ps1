<#
.SYNOPSIS
    Disables live scaling and restores KubePredict controller to safe dry-run mode.

.DESCRIPTION
    Restores CONTROLLER_MODE=dry_run and ENABLE_LIVE_SCALING=false in the ConfigMap.
    Restarts the controller deployment and waits for rollout.
    Verifies that live scaling is disabled.
#>
[CmdletBinding()]
param (
    [string]$Context = "kind-kubepredict-dev",
    [string]$Namespace = "kubepredict-system",
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"

Write-Host "=== Disabling Controller Live Scaling (Restoring Dry-Run) ===" -ForegroundColor Cyan

# 1. Verify exact context
$currentContext = kubectl config current-context
if ($currentContext -ne $Context) {
    Write-Error "Invalid context: Expected '$Context', got '$currentContext'."
}

# 2. Patch ConfigMap to dry-run
Write-Host "Resetting ConfigMap kubepredict-controller-config to dry-run defaults..."
$patchJson = '{"data":{"CONTROLLER_MODE":"dry_run","ENABLE_LIVE_SCALING":"false"}}'
kubectl --context $Context patch configmap kubepredict-controller-config -n $Namespace --type merge -p $patchJson
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to reset ConfigMap to dry_run mode"
}

# 3. Rollout restart controller
Write-Host "Restarting controller deployment to load dry-run configuration..."
kubectl --context $Context rollout restart deployment/kubepredict-controller -n $Namespace
kubectl --context $Context rollout status deployment/kubepredict-controller -n $Namespace --timeout="${TimeoutSeconds}s"

# 4. Verify dry-run mode active
$activeMode = kubectl --context $Context get configmap kubepredict-controller-config -n $Namespace -o jsonpath='{.data.CONTROLLER_MODE}'
$activeLive = kubectl --context $Context get configmap kubepredict-controller-config -n $Namespace -o jsonpath='{.data.ENABLE_LIVE_SCALING}'
if ($activeMode -ne "dry_run" -or $activeLive -ne "false") {
    Write-Error "Failed to restore dry-run defaults: mode='$activeMode', live='$activeLive'"
}

Write-Host "[PASS] Successfully restored controller to safe dry-run mode (CONTROLLER_MODE=dry_run, ENABLE_LIVE_SCALING=false)" -ForegroundColor Green

