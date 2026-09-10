$ErrorActionPreference = "Stop"
Write-Host "Verifying Docker reachability..."
docker info *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker is not reachable. Please start Docker Desktop."
}

Write-Host "Building kubepredict-workload-api:0.1.0..."
docker build --tag kubepredict-workload-api:0.1.0 .
