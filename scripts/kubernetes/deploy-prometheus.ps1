$ErrorActionPreference = "Stop"

$ExpectedCluster = "kubepredict-dev"
$ExpectedContext = "kind-kubepredict-dev"
$ManifestDir = "kubernetes/monitoring/prometheus"
$MonitoringNamespace = "kubepredict-monitoring"
$PrometheusDeployment = "kubepredict-prometheus"
$PrometheusService = "kubepredict-prometheus"
$WorkloadNamespace = "kubepredict-system"
$WorkloadDeployment = "kubepredict-workload-api"
$MetricsNamespace = "kube-system"
$MetricsDeployment = "metrics-server"

Write-Host "=== Deploying KubePredict Prometheus Monitoring Stack ==="

# 1. Verify Docker daemon
Write-Host "1. Verifying Docker daemon..."
docker version *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker daemon is unreachable. Please ensure Docker Desktop is running."
}

# 2. Verify kind CLI
Write-Host "2. Verifying kind CLI..."
kind version *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "kind CLI is not available in PATH."
}

# 3. Verify cluster exists
Write-Host "3. Verifying kind cluster '$ExpectedCluster'..."
$clusters = kind get clusters
if ($clusters -notcontains $ExpectedCluster) {
    Write-Error "Kind cluster '$ExpectedCluster' not found."
}

# 4. Verify current context
Write-Host "4. Verifying kubectl context..."
$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}

# 5. Verify all nodes are Ready
Write-Host "5. Verifying cluster nodes are Ready..."
kubectl wait --for=condition=Ready nodes --all --timeout=60s --context $ExpectedContext
if ($LASTEXITCODE -ne 0) {
    Write-Error "Cluster nodes are not in Ready state."
}

# 6. Verify fixed workload Deployment is healthy
Write-Host "6. Verifying fixed workload Deployment '$WorkloadDeployment'..."
$workloadReplicas = kubectl get deployment $WorkloadDeployment -n $WorkloadNamespace --context $ExpectedContext -o jsonpath="{.status.availableReplicas}" 2>$null
if ($workloadReplicas -ne "2") {
    Write-Error "Workload Deployment '$WorkloadDeployment' does not have 2 available replicas (found: '$workloadReplicas')."
}
$readyPods = (kubectl get pods -n $WorkloadNamespace --context $ExpectedContext -l app.kubernetes.io/component=workload-api -o jsonpath="{range .items[*]}{.status.conditions[?(@.type=='Ready')].status}{' '}{end}").Trim().Split(" ")
if ($readyPods.Count -ne 2 -or ($readyPods | Where-Object { $_ -ne "True" }).Count -gt 0) {
    Write-Error "Expected exactly 2 Ready workload pods in '$WorkloadNamespace'."
}

# 7. Verify no live HPA exists
Write-Host "7. Verifying no live HPA exists in '$WorkloadNamespace'..."
$hpa = kubectl get hpa -n $WorkloadNamespace --context $ExpectedContext --ignore-not-found=true
if ($hpa) {
    Write-Error "Conflicting live HPA detected in '$WorkloadNamespace'. Phase 6A requires the fixed 2-replica baseline without HPA."
}

# 8. Verify Metrics Server is healthy
Write-Host "8. Verifying Metrics Server Deployment and APIService..."
$msAvailable = kubectl get deployment $MetricsDeployment -n $MetricsNamespace --context $ExpectedContext -o jsonpath="{.status.availableReplicas}" 2>$null
if ($msAvailable -ne "1") {
    Write-Error "Metrics Server Deployment '$MetricsDeployment' is not available."
}
$apiStatus = kubectl get apiservice v1beta1.metrics.k8s.io --context $ExpectedContext -o jsonpath="{.status.conditions[?(@.type=='Available')].status}" 2>$null
if ($apiStatus -ne "True") {
    Write-Error "APIService v1beta1.metrics.k8s.io is not Available."
}

# 9. Render Prometheus Kustomization manifests
Write-Host "`n9. Rendering Prometheus Kustomize manifests from '$ManifestDir'..."
$rendered = kubectl kustomize $ManifestDir
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to render Kustomize manifests from '$ManifestDir'."
}
$resourceCount = ($rendered | Select-String -Pattern "^apiVersion:").Count
Write-Host "Successfully rendered $resourceCount resources from '$ManifestDir'."

# 10. Dry-run verification
Write-Host "10. Running client dry-run verification..."
kubectl apply --dry-run=client -k $ManifestDir --context $ExpectedContext
if ($LASTEXITCODE -ne 0) {
    Write-Error "Dry run failed for Prometheus manifests."
}

# 11. Check for conflicting pre-existing Prometheus deployment
Write-Host "11. Checking for existing conflicting live Prometheus resources..."
$existingDep = kubectl get deployment $PrometheusDeployment -n $MonitoringNamespace --context $ExpectedContext --ignore-not-found=true 2>$null
if ($existingDep) {
    Write-Warning "Prometheus Deployment '$PrometheusDeployment' already exists. Reapplying manifests..."
}

# 12. Apply Prometheus Kustomization
Write-Host "`n12. Applying Prometheus manifests to cluster..."
kubectl apply -k $ManifestDir --context $ExpectedContext
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to apply Prometheus manifests."
}

# 13. Wait for Prometheus rollout
Write-Host "`n13. Waiting for Prometheus Deployment rollout (bounded 240s timeout)..."
kubectl rollout status deployment/$PrometheusDeployment -n $MonitoringNamespace --context $ExpectedContext --timeout=240s
if ($LASTEXITCODE -ne 0) {
    Write-Error "Prometheus Deployment rollout failed."
}

# 14. Verify Service endpoints
Write-Host "`n14. Verifying Prometheus Service endpoints..."
$ep = kubectl get endpoints $PrometheusService -n $MonitoringNamespace --context $ExpectedContext -o jsonpath="{.subsets[*].addresses[*].ip}" 2>$null
if (-not $ep) {
    Write-Error "Prometheus Service '$PrometheusService' has no ready endpoints."
}
Write-Host "Prometheus endpoint active at: $ep"

Write-Host "`nPrometheus deployment completed successfully."
