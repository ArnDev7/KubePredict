$ErrorActionPreference = "Stop"

# Refresh PATH if kind is newly installed
if (-not (Get-Command kind -ErrorAction SilentlyContinue)) {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","User") + ";" + [System.Environment]::GetEnvironmentVariable("Path","Machine")
}

$ExpectedCluster = "kubepredict-dev"
$ExpectedContext = "kind-$ExpectedCluster"
$ManifestDir = "kubernetes/base"
$Namespace = "kubepredict-system"
$Deployment = "kubepredict-workload-api"
$ImageName = "kubepredict-workload-api:0.1.0"

Write-Host "=== Deploying Fixed-Replica Baseline ($ExpectedCluster) ==="

# 1. Verify context
$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}

# 2. Verify kind cluster exists
$clusters = kind get clusters
if ($clusters -notcontains $ExpectedCluster) {
    Write-Error "Kind cluster '$ExpectedCluster' not found."
}

# 3. Verify nodes are Ready
Write-Host "Verifying nodes readiness..."
kubectl wait --for=condition=Ready nodes --all --timeout=60s --context $ExpectedContext
if ($LASTEXITCODE -ne 0) {
    Write-Error "Cluster nodes are not in Ready state."
}

# 4. Verify image inside node
Write-Host "Verifying image in node runtime..."
$node = "kubepredict-dev-control-plane"
$nodeImages = docker exec $node crictl images
if ($nodeImages -notmatch "kubepredict-workload-api") {
    Write-Error "Image '$ImageName' not loaded into node '$node'. Run load-image.ps1 first."
}

# 5. Render Kustomize configuration before apply
Write-Host "`nRendering Kustomize manifests..."
kubectl kustomize $ManifestDir

# 6. Apply manifests
Write-Host "`nApplying fixed-replica baseline manifests to cluster..."
kubectl apply -k $ManifestDir --context $ExpectedContext
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to apply manifests."
}

# 7. Wait for deployment rollout
Write-Host "`nWaiting for Deployment rollout to complete (timeout 120s)..."
kubectl rollout status deployment/$Deployment -n $Namespace --timeout=120s --context $ExpectedContext
if ($LASTEXITCODE -ne 0) {
    Write-Error "Deployment rollout failed."
}

Write-Host "`nFixed-replica baseline deployed successfully."
kubectl get all -n $Namespace --context $ExpectedContext
