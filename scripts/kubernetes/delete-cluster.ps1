param (
    [switch]$ConfirmProjectCluster
)

$ErrorActionPreference = "Stop"

# Refresh PATH if kind is newly installed
if (-not (Get-Command kind -ErrorAction SilentlyContinue)) {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","User") + ";" + [System.Environment]::GetEnvironmentVariable("Path","Machine")
}

$ClusterName = "kubepredict-dev"

Write-Host "=== Delete KubePredict Cluster ==="

if (-not $ConfirmProjectCluster) {
    Write-Error "Safety check failed: You must provide -ConfirmProjectCluster to delete cluster '$ClusterName'."
}

Write-Host "Listing existing kind clusters..."
$existingClusters = kind get clusters
if ($existingClusters -notcontains $ClusterName) {
    Write-Host "Cluster '$ClusterName' does not exist. Nothing to delete."
    return
}

Write-Host "Deleting kind cluster '$ClusterName'..."
kind delete cluster --name $ClusterName
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to delete kind cluster '$ClusterName'."
}

Write-Host "Cluster '$ClusterName' has been deleted successfully."
