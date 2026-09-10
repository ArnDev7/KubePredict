param (
    [switch]$ConfirmFixedBaselineRemoval
)

$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$ManifestDir = "kubernetes/base"
$Namespace = "kubepredict-system"

Write-Host "=== Removing Fixed-Replica Baseline Resources ==="

if (-not $ConfirmFixedBaselineRemoval) {
    Write-Error "Safety check failed: You must specify -ConfirmFixedBaselineRemoval to delete fixed baseline resources."
}

# Verify context
$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}

Write-Host "Deleting resources defined in '$ManifestDir'..."
kubectl delete -k $ManifestDir --context $ExpectedContext --ignore-not-found=true
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to delete fixed-replica baseline resources."
}

Write-Host "Fixed-replica baseline resources have been removed successfully."
