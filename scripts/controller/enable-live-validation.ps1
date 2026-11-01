<#
.SYNOPSIS
    Guarded script to temporarily enable live scaling for bounded Phase 11 validation.

.DESCRIPTION
    Requires explicit -ConfirmBoundedLiveScaling confirmation switch.
    Performs comprehensive safety pre-flight verification before updating the controller ConfigMap
    to CONTROLLER_MODE=live and ENABLE_LIVE_SCALING=true.
#>
[CmdletBinding()]
param (
    [switch]$ConfirmBoundedLiveScaling,
    [string]$Context = "kind-kubepredict-dev",
    [string]$Namespace = "kubepredict-system",
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"

if (-not $ConfirmBoundedLiveScaling) {
    Write-Error "Safety Gate: You must specify -ConfirmBoundedLiveScaling to enable live controller writes."
}

Write-Host "=== Enabling Bounded Live Scaling (Phase 11 Validation) ===" -ForegroundColor Yellow

# 1. Verify exact context
$currentContext = kubectl config current-context
if ($currentContext -ne $Context) {
    Write-Error "Invalid context: Expected '$Context', got '$currentContext'."
}
Write-Host "[PASS] Context verified: $Context" -ForegroundColor Green

# 2. Verify controller currently deployed
$ctrlReplicas = kubectl --context $Context get deployment kubepredict-controller -n $Namespace -o jsonpath='{.status.readyReplicas}' 2>$null
if ($ctrlReplicas -ne "1") {
    Write-Error "Controller must have exactly 1 Ready replica before live enablement. Observed: '$ctrlReplicas'"
}
Write-Host "[PASS] Controller Deployment has exactly 1 Ready replica" -ForegroundColor Green

# 3. Verify controller is in dry_run mode
$cmMode = kubectl --context $Context get configmap kubepredict-controller-config -n $Namespace -o jsonpath='{.data.CONTROLLER_MODE}'
$cmLive = kubectl --context $Context get configmap kubepredict-controller-config -n $Namespace -o jsonpath='{.data.ENABLE_LIVE_SCALING}'
Write-Host "Current controller settings: CONTROLLER_MODE=$cmMode, ENABLE_LIVE_SCALING=$cmLive"

# 4. Verify no HPA
$hpas = kubectl --context $Context get hpa -A --no-headers 2>$null
if ($hpas) {
    Write-Error "Safety violation: Live HPA detected. Refusing to enable live controller."
}
Write-Host "[PASS] No HPA detected" -ForegroundColor Green

# 5. Verify workload health
$readyReplicas = kubectl --context $Context get deployment kubepredict-workload-api -n $Namespace -o jsonpath='{.status.readyReplicas}'
if ($readyReplicas -ne "2") {
    Write-Error "Workload must have exactly 2 Ready replicas. Observed: '$readyReplicas'"
}
Write-Host "[PASS] Workload has 2 Ready replicas" -ForegroundColor Green

# 6. Verify exact RBAC
$canPatchScale = kubectl --context $Context auth can-i patch deployments/scale --as=system:serviceaccount:$Namespace:kubepredict-controller -n $Namespace
if ($canPatchScale.Trim() -ne "yes") {
    Write-Error "RBAC check failed: kubepredict-controller cannot patch deployments/scale"
}
$canPatchDeploy = kubectl --context $Context auth can-i patch deployment --as=system:serviceaccount:$Namespace:kubepredict-controller -n $Namespace
if ($canPatchDeploy.Trim() -eq "yes") {
    Write-Error "RBAC violation: kubepredict-controller has forbidden permission to patch full deployment"
}
Write-Host "[PASS] RBAC least-privilege permissions verified" -ForegroundColor Green

# 7. Update ConfigMap to live
Write-Host "`nPatching ConfigMap kubepredict-controller-config with live switches..."
$patchJson = '{"data":{"CONTROLLER_MODE":"live","ENABLE_LIVE_SCALING":"true"}}'
kubectl --context $Context patch configmap kubepredict-controller-config -n $Namespace --type merge -p $patchJson
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to patch ConfigMap to live mode"
}

# 8. Restart controller deployment to pick up ConfigMap change
Write-Host "Restarting controller deployment to load new configuration..."
kubectl --context $Context rollout restart deployment/kubepredict-controller -n $Namespace
kubectl --context $Context rollout status deployment/kubepredict-controller -n $Namespace --timeout="${TimeoutSeconds}s"

# 9. Verify live mode activated
$activeMode = kubectl --context $Context get configmap kubepredict-controller-config -n $Namespace -o jsonpath='{.data.CONTROLLER_MODE}'
$activeLive = kubectl --context $Context get configmap kubepredict-controller-config -n $Namespace -o jsonpath='{.data.ENABLE_LIVE_SCALING}'
if ($activeMode -ne "live" -or $activeLive -ne "true") {
    Write-Error "Failed to confirm live switches in ConfigMap: mode='$activeMode', live='$activeLive'"
}

Write-Host "`n[ALERT] CONTROLLER IS NOW IN LIVE-WRITE MODE (CONTROLLER_MODE=live, ENABLE_LIVE_SCALING=true)" -ForegroundColor Magenta
Write-Host "[ALERT] Disable immediately upon completing bounded validation via disable-live-scaling.ps1" -ForegroundColor Magenta

