$ErrorActionPreference = "Stop"
Write-Host "Verifying Docker reachability..."
docker info *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker is not reachable. Please start Docker Desktop."
}

try {
    docker rm -f apa-workload-api *>$null
} catch {}

Write-Host "Starting apa-workload-api..."
docker run -d `
    --name apa-workload-api `
    -p 8000:8000 `
    --memory 512m `
    --cpus 1.0 `
    --read-only `
    --tmpfs /tmp `
    --security-opt no-new-privileges:true `
    --label org.opencontainers.image.title="adaptive-predictive-k8s-autoscaler" `
    --label org.opencontainers.image.version="0.1.0" `
    --label com.apa.project="adaptive-predictive-k8s-autoscaler" `
    --label com.apa.component="workload-api" `
    -e APP_HOST="0.0.0.0" `
    -e APP_PORT="8000" `
    -e LOG_LEVEL="INFO" `
    -e CONTROLLER_MODE="observe" `
    -e ENABLE_LIVE_SCALING="false" `
    kubepredict-workload-api:0.1.0
