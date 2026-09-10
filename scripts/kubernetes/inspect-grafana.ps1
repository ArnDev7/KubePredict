param (
    [int]$LocalPort = 0
)

$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$MonitoringNamespace = "kubepredict-monitoring"
$GrafanaDeployment = "kubepredict-grafana"
$GrafanaService = "kubepredict-grafana"
$SecretName = "grafana-admin-credentials"

Write-Host "=== Inspecting KubePredict Grafana Deployment ==="

# 1. Exact context
$currentContext = (kubectl config current-context).Trim()
Write-Host "1. Current Context: $currentContext"
if ($currentContext -ne $ExpectedContext) {
    Write-Warning "Current context does not match expected context '$ExpectedContext'."
}

# 2. Deployment status
Write-Host "`n2. Deployment Status ($GrafanaDeployment):"
kubectl get deployment $GrafanaDeployment -n $MonitoringNamespace --context $ExpectedContext -o wide

# 3. Pod status, image, and restart count
Write-Host "`n3. Pod Status & Restart Count:"
$pods = kubectl get pods -n $MonitoringNamespace --context $ExpectedContext -l app.kubernetes.io/component=monitoring-dashboard -o wide
Write-Host $pods

# 4. Container resources and image
Write-Host "`n4. Container Spec (Image & Resources):"
kubectl get deployment $GrafanaDeployment -n $MonitoringNamespace --context $ExpectedContext -o jsonpath="{range .spec.template.spec.containers[*]}Image: {.image}{'\n'}Requests: {.resources.requests}{'\n'}Limits: {.resources.limits}{'\n'}{end}"

# 5. Service status and endpoints
Write-Host "`n5. Service Status & Endpoints:"
$ep = kubectl get endpointslice -n $MonitoringNamespace -l "kubernetes.io/service-name=$GrafanaService" --context $ExpectedContext -o jsonpath="{.items[*].endpoints[*].addresses[*]}:{range .items[*].ports[*]}{.port}{end}" 2>$null
if (-not $ep) {
    $ep = (kubectl get endpoints $GrafanaService -n $MonitoringNamespace --context $ExpectedContext -o jsonpath="{.subsets[*].addresses[*].ip}:{.subsets[*].ports[*].port}" 2>&1 | Where-Object { $_ -notmatch "Warning" })
}
Write-Host "Endpoints: $ep"

# 6. Secret existence (without printing contents)
Write-Host "`n6. Secret Verification:"
$secretExists = kubectl get secret $SecretName -n $MonitoringNamespace --context $ExpectedContext --ignore-not-found=true
if ($secretExists) {
    Write-Host "Secret '$SecretName' exists in '$MonitoringNamespace'. (Data keys: $(kubectl get secret $SecretName -n $MonitoringNamespace --context $ExpectedContext -o jsonpath='{.data}'))"
} else {
    Write-Warning "Secret '$SecretName' was not found in '$MonitoringNamespace'."
}

# 7. Provisioning ConfigMaps
Write-Host "`n7. Provisioning ConfigMaps:"
kubectl get configmaps -n $MonitoringNamespace --context $ExpectedContext -l app.kubernetes.io/managed-by=kustomize

# 8. Recent events
Write-Host "`n8. Recent Grafana Events:"
kubectl get events -n $MonitoringNamespace --context $ExpectedContext --field-selector involvedObject.name=$GrafanaDeployment --sort-by='.metadata.creationTimestamp' | Select-Object -Last 10

# 9. Bounded recent logs
Write-Host "`n9. Bounded Recent Logs (last 25 lines):"
kubectl logs deployment/$GrafanaDeployment -n $MonitoringNamespace --context $ExpectedContext --tail=25 2>$null

# 10. Local API inspection if LocalPort is specified
if ($LocalPort -gt 0) {
    Write-Host "`n10. Querying Local Grafana API on port $LocalPort..."
    try {
        $healthUrl = "http://127.0.0.1:$LocalPort/api/health"
        $healthResp = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 5
        Write-Host "Grafana Health: $($healthResp | ConvertTo-Json -Compress)"
    } catch {
        Write-Warning "Failed to contact Grafana health endpoint at $($healthUrl): $_"
    }
}
