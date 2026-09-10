$ErrorActionPreference = "Stop"

# Refresh PATH if kind is newly installed
if (-not (Get-Command kind -ErrorAction SilentlyContinue)) {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","User") + ";" + [System.Environment]::GetEnvironmentVariable("Path","Machine")
}

$ClusterName = "kubepredict-dev"
$ConfigFile = "kubernetes/kind/cluster.yaml"
$ExpectedContext = "kind-$ClusterName"

Write-Host "=== Creating KubePredict Cluster ==="

# List existing kind clusters first
Write-Host "Checking existing kind clusters..."
$existingClusters = kind get clusters
if ($existingClusters -contains $ClusterName) {
    Write-Host "Cluster '$ClusterName' already exists. Verifying cluster state..."
} else {
    if (-not (Test-Path $ConfigFile)) {
        Write-Error "Cluster configuration file '$ConfigFile' not found."
    }
    Write-Host "Creating kind cluster '$ClusterName' with configuration '$ConfigFile'..."
    kind create cluster --name $ClusterName --config $ConfigFile
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create kind cluster '$ClusterName'."
    }
}

# Verify current context after creation
Write-Host "`nVerifying kubectl current context..."
$currentContext = (kubectl config current-context).Trim()
Write-Host "Current context: $currentContext"
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}

# Verify cluster-info
Write-Host "`nVerifying cluster-info..."
kubectl cluster-info --context $ExpectedContext
if ($LASTEXITCODE -ne 0) {
    Write-Error "kubectl cluster-info failed for context '$ExpectedContext'."
}

# Verify node readiness with bounded wait
Write-Host "`nWaiting for all nodes to become Ready (timeout 120s)..."
kubectl wait --for=condition=Ready nodes --all --timeout=120s --context $ExpectedContext
if ($LASTEXITCODE -ne 0) {
    Write-Error "Timed out waiting for nodes to become Ready."
}

Write-Host "`nCluster nodes:"
kubectl get nodes --context $ExpectedContext -o wide

Write-Host "`nCluster '$ClusterName' is verified and ready."
