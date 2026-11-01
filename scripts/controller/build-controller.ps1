<#
.SYNOPSIS
    Builds and loads the KubePredict controller Docker image (Phase 11).

.DESCRIPTION
    Builds the versioned kubepredict-controller:0.1.0 image using Dockerfile.controller.
    Inspects and asserts that the container runs as a non-root user.
    Loads the image into kind cluster kubepredict-dev.
    Verifies image presence across all kind cluster nodes.
#>
[CmdletBinding()]
param (
    [string]$ImageTag = "kubepredict-controller:0.1.0",
    [string]$ClusterName = "kubepredict-dev"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

Write-Host "=== Building KubePredict Controller Image ($ImageTag) ===" -ForegroundColor Cyan

# 1. Build image
Push-Location $RepoRoot
try {
    Write-Host "Executing docker build..."
    docker build -f Dockerfile.controller -t $ImageTag .
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Docker build failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

# 2. Inspect non-root user
Write-Host "`nVerifying non-root user configuration..."
$user = docker image inspect $ImageTag --format '{{.Config.User}}'
if ($user -ne "appuser" -and $user -ne "1000") {
    Write-Error "Security failure: Image must run as non-root user. Observed: '$user'"
}
Write-Host "[PASS] Image runs as non-root user: $user" -ForegroundColor Green

# 3. Load image into kind
Write-Host "`nLoading image into kind cluster '$ClusterName'..."
kind load docker-image $ImageTag --name $ClusterName
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to load $ImageTag into kind cluster $ClusterName"
}

# 4. Verify image on all kind nodes
Write-Host "`nVerifying image presence inside kind nodes..."
$nodes = docker ps --filter "label=io.x-k8s.kind.cluster=$ClusterName" --format "{{.Names}}"
foreach ($node in $nodes) {
    Write-Host "Checking node: $node..."
    $images = docker exec $node crictl images
    if ($images -match "kubepredict-controller" -and $images -match "0.1.0") {
        Write-Host "[PASS] Verified $ImageTag on node $node" -ForegroundColor Green
    } else {
        Write-Error "Image $ImageTag not found on node $node"
    }
}

Write-Host "`n=== Controller Image Build and Verification Complete ===" -ForegroundColor Green

