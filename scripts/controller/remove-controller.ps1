<#
.SYNOPSIS
    Removes KubePredict controller resources from the cluster (Phase 11).

.DESCRIPTION
    Requires explicit -ConfirmControllerRemoval confirmation.
    Deletes only controller resources (Deployment, Service, ConfigMap, Role, RoleBinding, ServiceAccount).
    Does NOT remove workload, monitoring, Metrics Server, namespace, or cluster.
#>
[CmdletBinding()]
param (
    [switch]$ConfirmControllerRemoval,
    [string]$Context = "kind-kubepredict-dev",
    [string]$Namespace = "kubepredict-system"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

if (-not $ConfirmControllerRemoval) {
    Write-Error "Safety Gate: You must specify -ConfirmControllerRemoval to remove controller resources."
}

Write-Host "=== Removing KubePredict Controller Resources ===" -ForegroundColor Cyan

# 1. Verify exact context
$currentContext = kubectl config current-context
if ($currentContext -ne $Context) {
    Write-Error "Invalid context: Expected '$Context', got '$currentContext'."
}

# 2. Delete controller resources using kustomize
$manifestPath = Join-Path $RepoRoot "kubernetes\controller"
Write-Host "Deleting controller resources from '$manifestPath'..."
kubectl --context $Context delete -k $manifestPath --ignore-not-found=true
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to delete controller resources"
}

Write-Host "[PASS] Controller resources removed successfully." -ForegroundColor Green
Write-Host "Workload, monitoring, metrics-server, and namespace remain intact." -ForegroundColor Green

