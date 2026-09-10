$ErrorActionPreference = "Stop"

# Refresh PATH if kind is newly installed
if (-not (Get-Command kind -ErrorAction SilentlyContinue)) {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","User") + ";" + [System.Environment]::GetEnvironmentVariable("Path","Machine")
}

$ExpectedCluster = "kubepredict-dev"
$ExpectedContext = "kind-$ExpectedCluster"
$ManifestDir = "kubernetes/metrics-server"
$Namespace = "kube-system"
$Deployment = "metrics-server"

Write-Host "=== Installing Kubernetes Metrics Server ($ExpectedCluster) ==="

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

# 6. Render Kustomize manifests
Write-Host "`n6. Rendering Metrics Server Kustomize manifests..."
kubectl kustomize $ManifestDir
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to render Kustomize manifests from '$ManifestDir'."
}

# 7. Apply Metrics Server
Write-Host "`n7. Applying Metrics Server to cluster..."
kubectl apply -k $ManifestDir --context $ExpectedContext
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to apply Metrics Server manifests."
}

# 8. Bounded rollout wait for Deployment
Write-Host "`n8. Waiting for Metrics Server Deployment rollout (timeout: 180s)..."
kubectl rollout status deployment/$Deployment -n $Namespace --timeout=180s --context $ExpectedContext
if ($LASTEXITCODE -ne 0) {
    Write-Error "Metrics Server Deployment rollout failed."
}

# 9. Bounded polling for APIService availability
Write-Host "`n9. Verifying APIService v1beta1.metrics.k8s.io availability (bounded polling)..."
$maxRetries = 24
$delaySeconds = 5
$apiAvailable = $false

for ($i = 1; $i -le $maxRetries; $i++) {
    $status = kubectl get apiservice v1beta1.metrics.k8s.io --context $ExpectedContext -o jsonpath="{.status.conditions[?(@.type=='Available')].status}" 2>$null
    if ($status -eq "True") {
        $apiAvailable = $true
        Write-Host "APIService v1beta1.metrics.k8s.io is Available (attempt $i of $maxRetries)."
        break
    }
    Write-Host "Waiting for APIService to report Available=True (attempt $i of $maxRetries)..."
    Start-Sleep -Seconds $delaySeconds
}

if (-not $apiAvailable) {
    Write-Error "APIService v1beta1.metrics.k8s.io failed to become Available within bounded timeout."
}

Write-Host "`nMetrics Server installation and validation completed successfully."
