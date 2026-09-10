$ErrorActionPreference = "Stop"
Write-Host "Verifying Docker reachability..."
docker info *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker is not reachable. Please start Docker Desktop."
}

Write-Host "Building adaptive-predictive-k8s-autoscaler:0.1.0..."
docker build --tag adaptive-predictive-k8s-autoscaler:0.1.0 .
