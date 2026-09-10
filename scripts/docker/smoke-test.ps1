$ErrorActionPreference = "Stop"

Write-Host "Waiting for container to become healthy..."
$MaxRetries = 10
$RetryCount = 0
$IsHealthy = $false

while ($RetryCount -lt $MaxRetries) {
    $Status = docker inspect --format="{{.State.Health.Status}}" apa-workload-api
    if ($Status -eq "healthy") {
        $IsHealthy = $true
        break
    }
    Write-Host "Status: $Status - retrying in 2 seconds..."
    Start-Sleep -Seconds 2
    $RetryCount++
}

if (-not $IsHealthy) {
    Write-Error "Container failed to become healthy."
}

Write-Host "Probing /..."
Invoke-RestMethod -Uri http://127.0.0.1:8000/

Write-Host "Probing /healthz..."
Invoke-RestMethod -Uri http://127.0.0.1:8000/healthz

Write-Host "Probing /readyz..."
Invoke-RestMethod -Uri http://127.0.0.1:8000/readyz

Write-Host "Probing /work..."
Invoke-RestMethod -Uri http://127.0.0.1:8000/work -Method Post -Body '{"cpu_iterations": 1000, "delay_ms": 10, "payload_size": 10}' -ContentType "application/json"

Write-Host "Probing /metrics..."
$Metrics = Invoke-RestMethod -Uri http://127.0.0.1:8000/metrics
Write-Host ($Metrics | Select-Object -First 10)

Write-Host "Smoke test completed successfully."
