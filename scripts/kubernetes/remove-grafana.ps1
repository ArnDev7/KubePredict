param (
    [switch]$ConfirmGrafanaRemoval
)

$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$ManifestDir = "kubernetes/monitoring/grafana"
$MonitoringNamespace = "kubepredict-monitoring"
$SecretName = "grafana-admin-credentials"

Write-Host "=== Removing KubePredict Grafana Resources ==="

if (-not $ConfirmGrafanaRemoval) {
    Write-Error "Grafana removal requires explicit confirmation. Please specify -ConfirmGrafanaRemoval."
}

# 1. Verify context
$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}

# 2. Delete Grafana Kustomize resources only
Write-Host "Deleting Grafana Kustomize resources..."
kubectl delete -k $ManifestDir --context $ExpectedContext --ignore-not-found=true

# 3. Delete runtime secret
Write-Host "Deleting runtime secret '$SecretName'..."
kubectl delete secret $SecretName -n $MonitoringNamespace --context $ExpectedContext --ignore-not-found=true

Write-Host "Grafana resources removed. Namespace '$MonitoringNamespace', Prometheus, and workload resources remain intact."
