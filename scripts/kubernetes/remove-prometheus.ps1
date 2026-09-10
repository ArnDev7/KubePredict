param (
    [switch]$ConfirmPrometheusRemoval
)

$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$ManifestDir = "kubernetes/monitoring/prometheus"

Write-Host "=== Removing Prometheus Monitoring Resources ==="

if (-not $ConfirmPrometheusRemoval) {
    Write-Error "Safety check failed: You must specify -ConfirmPrometheusRemoval to remove Prometheus resources."
}

# 1. Verify context
$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}

Write-Host "Deleting Prometheus resources defined in '$ManifestDir'..."
kubectl delete -k $ManifestDir --context $ExpectedContext --ignore-not-found=true
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to delete Prometheus resources."
}

Write-Host "`nPrometheus monitoring resources have been removed successfully."
