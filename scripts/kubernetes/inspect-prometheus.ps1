$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$MonitoringNamespace = "kubepredict-monitoring"
$PrometheusDeployment = "kubepredict-prometheus"
$PrometheusService = "kubepredict-prometheus"
$ClusterRole = "kubepredict-prometheus-discovery"

Write-Host "=== Inspecting Prometheus Monitoring Deployment ==="

# 1. Verify context
$currentContext = (kubectl config current-context).Trim()
Write-Host "Context:   $currentContext"
if ($currentContext -ne $ExpectedContext) {
    Write-Warning "Current context does not match expected context '$ExpectedContext'."
}
Write-Host "Namespace: $MonitoringNamespace"

# 2. Deployment status
Write-Host "`n--- Deployment Status ---"
kubectl get deployment $PrometheusDeployment -n $MonitoringNamespace --context $ExpectedContext -o wide

# 3. Pod status
Write-Host "`n--- Pod Status ---"
kubectl get pods -n $MonitoringNamespace --context $ExpectedContext -l app.kubernetes.io/component=monitoring -o wide

# 4. Pod resources, images, and restart counts
Write-Host "`n--- Detailed Pod Specifications ---"
$podJson = kubectl get pods -n $MonitoringNamespace --context $ExpectedContext -l app.kubernetes.io/component=monitoring -o json | ConvertFrom-Json
if ($podJson.items -and $podJson.items.Count -gt 0) {
    foreach ($pod in $podJson.items) {
        Write-Host "Pod Name:      $($pod.metadata.name)"
        Write-Host "Status:        $($pod.status.phase)"
        Write-Host "Node:          $($pod.spec.nodeName)"
        Write-Host "Pod IP:        $($pod.status.podIP)"
        foreach ($c in $pod.spec.containers) {
            Write-Host "Container:     $($c.name)"
            Write-Host "Image:         $($c.image)"
            Write-Host "CPU Request:   $($c.resources.requests.cpu)"
            Write-Host "CPU Limit:     $($c.resources.limits.cpu)"
            Write-Host "Memory Req:    $($c.resources.requests.memory)"
            Write-Host "Memory Limit:  $($c.resources.limits.memory)"
        }
        foreach ($cs in $pod.status.containerStatuses) {
            Write-Host "Ready:         $($cs.ready)"
            Write-Host "Restarts:      $($cs.restartCount)"
        }
    }
} else {
    Write-Host "No monitoring pods found."
}

# 5. Service and Endpoints
Write-Host "`n--- Service Status ---"
kubectl get service $PrometheusService -n $MonitoringNamespace --context $ExpectedContext -o wide
Write-Host "`n--- Service Endpoints ---"
kubectl get endpoints $PrometheusService -n $MonitoringNamespace --context $ExpectedContext

# 6. ClusterRole Rules
Write-Host "`n--- ClusterRole RBAC Rules ---"
kubectl describe clusterrole $ClusterRole

# 7. Recent Events
Write-Host "`n--- Recent Monitoring Events ---"
kubectl get events -n $MonitoringNamespace --context $ExpectedContext --sort-by=.metadata.creationTimestamp

# 8. Bounded Recent Logs
Write-Host "`n--- Recent Prometheus Logs (last 30 lines) ---"
kubectl logs deployment/$PrometheusDeployment -n $MonitoringNamespace --context $ExpectedContext --tail=30
