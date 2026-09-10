$ErrorActionPreference = "Stop"
Write-Host "Inspecting apa-workload-api..."
docker inspect apa-workload-api
Write-Host "Fetching logs for apa-workload-api..."
docker logs apa-workload-api
