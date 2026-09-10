$ErrorActionPreference = "Stop"

# Refresh PATH if kind is newly installed
if (-not (Get-Command kind -ErrorAction SilentlyContinue)) {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","User") + ";" + [System.Environment]::GetEnvironmentVariable("Path","Machine")
}

$ClusterName = "kubepredict-dev"
$ImageName = "kubepredict-workload-api:0.1.0"

Write-Host "=== Loading Image into KubePredict Cluster ==="

# Verify cluster exists
Write-Host "Checking if kind cluster '$ClusterName' exists..."
$existingClusters = kind get clusters
if ($existingClusters -notcontains $ClusterName) {
    Write-Error "Kind cluster '$ClusterName' does not exist. Run create-cluster.ps1 first."
}

# Verify image exists in Docker before loading
Write-Host "Checking local Docker image '$ImageName'..."
$imgId = docker images -q $ImageName
if (-not $imgId) {
    Write-Error "Local Docker image '$ImageName' not found. Please build or tag it before loading."
}
Write-Host "Found Docker image '$ImageName' (ID: $imgId)."

# Load image into kind cluster
Write-Host "`nLoading image '$ImageName' into kind cluster '$ClusterName'..."
kind load docker-image $ImageName --name $ClusterName
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to load image '$ImageName' into cluster '$ClusterName'."
}

# Verify image presence in nodes via crictl
Write-Host "`nVerifying image presence inside cluster nodes..."
$nodes = docker ps --filter "label=io.x-k8s.kind.cluster=$ClusterName" --format "{{.Names}}"
foreach ($node in $nodes) {
    Write-Host "Checking node container: $node..."
    $nodeImages = docker exec $node crictl images
    if ($nodeImages -match "kubepredict-workload-api") {
        Write-Host "Image '$ImageName' successfully verified on node '$node'."
    } else {
        Write-Error "Image '$ImageName' not found on node '$node'."
    }
}

Write-Host "`nImage loading and verification complete."
