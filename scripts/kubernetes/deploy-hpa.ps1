$ErrorActionPreference = "Stop"

# Refresh PATH if kind is newly installed
if (-not (Get-Command kind -ErrorAction SilentlyContinue)) {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","User") + ";" + [System.Environment]::GetEnvironmentVariable("Path","Machine")
}

$ExpectedCluster = "kubepredict-dev"
$ExpectedContext = "kind-kubepredict-dev"
$ManifestDir = "kubernetes/hpa"
$WorkloadNamespace = "kubepredict-system"
$WorkloadDeployment = "kubepredict-workload-api"
$MetricsNamespace = "kube-system"
$MetricsDeployment = "metrics-server"
$HpaName = "kubepredict-workload-api"

Write-Host "=== Deploying Kubernetes HPA Baseline ($ExpectedCluster) ==="

# 1. Verify Docker server
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

# 5. Verify nodes are Ready
Write-Host "5. Verifying cluster nodes are Ready..."
kubectl wait --for=condition=Ready nodes --all --timeout=60s --context $ExpectedContext
if ($LASTEXITCODE -ne 0) {
    Write-Error "Cluster nodes are not in Ready state."
}

# 6. Verify fixed workload Deployment is available
Write-Host "6. Verifying fixed workload Deployment '$WorkloadDeployment'..."
$workloadReplicas = kubectl get deployment $WorkloadDeployment -n $WorkloadNamespace --context $ExpectedContext -o jsonpath="{.status.availableReplicas}" 2>$null
if ($workloadReplicas -ne "2") {
    Write-Error "Workload Deployment '$WorkloadDeployment' does not have 2 available replicas (found: '$workloadReplicas')."
}

# 7. Verify workload pods are Ready
Write-Host "7. Verifying workload pods are Ready..."
$readyPods = (kubectl get pods -n $WorkloadNamespace --context $ExpectedContext -l app.kubernetes.io/component=workload-api -o jsonpath="{range .items[*]}{.status.conditions[?(@.type=='Ready')].status}{' '}{end}").Trim().Split(" ")
if ($readyPods.Count -ne 2 -or ($readyPods | Where-Object { $_ -ne "True" }).Count -gt 0) {
    Write-Error "Expected exactly 2 Ready workload pods in '$WorkloadNamespace'."
}

# 8. Verify Metrics Server Deployment is Ready
Write-Host "8. Verifying Metrics Server Deployment..."
$msAvailable = kubectl get deployment $MetricsDeployment -n $MetricsNamespace --context $ExpectedContext -o jsonpath="{.status.availableReplicas}" 2>$null
if ($msAvailable -ne "1") {
    Write-Error "Metrics Server Deployment '$MetricsDeployment' is not available."
}

# 9. Verify metrics APIService is Available
Write-Host "9. Verifying APIService v1beta1.metrics.k8s.io..."
$apiStatus = kubectl get apiservice v1beta1.metrics.k8s.io --context $ExpectedContext -o jsonpath="{.status.conditions[?(@.type=='Available')].status}" 2>$null
if ($apiStatus -ne "True") {
    Write-Error "APIService v1beta1.metrics.k8s.io is not Available."
}

# 10. Verify kubectl top works for workload pods
Write-Host "10. Verifying kubectl top on workload pods..."
kubectl top pods -n $WorkloadNamespace --context $ExpectedContext >$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to retrieve pod metrics via kubectl top."
}

# 11. Render Kustomize manifests and verify exactly one HPA
Write-Host "`n11. Rendering HPA Kustomize manifests..."
$rendered = kubectl kustomize $ManifestDir
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to render Kustomize manifests from '$ManifestDir'."
}
$hpaCount = ($rendered | Select-String -Pattern "kind: HorizontalPodAutoscaler").Count
if ($hpaCount -ne 1) {
    Write-Error "Expected exactly 1 HorizontalPodAutoscaler resource rendered, but found $hpaCount."
}

# 12. Verify no live conflicting HPA
Write-Host "12. Checking for existing conflicting live HPA..."
$existingHpa = kubectl get hpa $HpaName -n $WorkloadNamespace --context $ExpectedContext --ignore-not-found=true
if ($existingHpa) {
    Write-Error "An HPA named '$HpaName' already exists in namespace '$WorkloadNamespace'."
}

# 13. Apply HPA manifest
Write-Host "`n13. Applying HPA to cluster..."
kubectl apply -k $ManifestDir --context $ExpectedContext
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to apply HPA manifests."
}

# 14. Bounded polling for HPA current metrics
Write-Host "`n14. Waiting for HPA to resolve current metrics (bounded polling)..."
$maxRetries = 24
$delaySeconds = 5
$hpaMetricsResolved = $false

for ($i = 1; $i -le $maxRetries; $i++) {
    $currentMetric = kubectl get hpa $HpaName -n $WorkloadNamespace --context $ExpectedContext -o jsonpath="{.status.currentMetrics[0].resource.current.averageUtilization}" 2>$null
    if ($currentMetric -and $currentMetric -ne "<unknown>") {
        $hpaMetricsResolved = $true
        Write-Host "HPA metrics resolved: current CPU utilization is $currentMetric% (attempt $i of $maxRetries)."
        break
    }
    Write-Host "Waiting for HPA current metrics to resolve (attempt $i of $maxRetries)..."
    Start-Sleep -Seconds $delaySeconds
}

if (-not $hpaMetricsResolved) {
    Write-Warning "HPA current metrics were not resolved within the bounded wait period. Metrics Server may require an additional scraping cycle."
}

Write-Host "`nHPA baseline deployment completed successfully."
