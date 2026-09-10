$ErrorActionPreference = "Stop"
Write-Host "Stopping and removing apa-workload-api..."
try {
    docker rm -f apa-workload-api *>$null
} catch {}
Write-Host "Container stopped."
