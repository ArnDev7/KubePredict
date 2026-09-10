param (
    [switch]$ConfirmHpaRemoval
)

$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$ManifestDir = "kubernetes/hpa"
$Namespace = "kubepredict-system"
$Deployment = "kubepredict-workload-api"

Write-Host "=== Removing Kubernetes HPA Baseline ==="

if (-not $ConfirmHpaRemoval) {
    Write-Error "Safety check failed: You must specify -ConfirmHpaRemoval to delete HPA resources."
}

# Verify context
$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}

Write-Host "Deleting HPA resources defined in '$ManifestDir' from namespace '$Namespace'..."
kubectl delete -k $ManifestDir --context $ExpectedContext --ignore-not-found=true
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to delete HPA resources."
}

Write-Host "`nHPA resources have been removed successfully."
Write-Host "NOTE: Removing the HPA does not alter current Deployment replicas."
Write-Host "To explicitly restore the 2-replica fixed baseline, run: kubectl scale deployment $Deployment --replicas=2 -n $Namespace --context $ExpectedContext"
