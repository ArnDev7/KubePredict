param (
    [switch]$ConfirmMetricsServerRemoval
)

$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$ManifestDir = "kubernetes/metrics-server"

Write-Host "=== Removing Kubernetes Metrics Server ==="

if (-not $ConfirmMetricsServerRemoval) {
    Write-Error "Safety check failed: You must specify -ConfirmMetricsServerRemoval to delete Metrics Server resources."
}

# Verify context
$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}

Write-Host "Deleting resources defined in '$ManifestDir'..."
kubectl delete -k $ManifestDir --context $ExpectedContext --ignore-not-found=true
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to delete Metrics Server resources."
}

Write-Host "Metrics Server resources have been removed successfully."
