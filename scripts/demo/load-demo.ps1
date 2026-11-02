<#
.SYNOPSIS
    Generates bounded demo traffic against the workload API.
.DESCRIPTION
    Reads the port from .demo-state/state.json and sends:
      - GET /
      - GET /healthz
      - GET /readyz
      - 5x POST /work  (safe payload)
    Optionally runs Locust for 30 s at 2 users if a demo locustfile exists.
#>

$ErrorActionPreference = "Stop"
$StateFile = ".demo-state\state.json"

if (-not (Test-Path $StateFile)) {
    throw "Demo state file not found at $StateFile. Run start-demo.ps1 first."
}

$state   = Get-Content $StateFile -Raw | ConvertFrom-Json
$apiPort = $state.api.port
$baseUrl = "http://127.0.0.1:$apiPort"

Write-Host "Generating demo traffic against $baseUrl" -ForegroundColor Cyan
Start-Sleep -Seconds 2   # let port-forward settle

# GET requests
foreach ($path in @("/", "/healthz", "/readyz")) {
    try {
        $r = Invoke-RestMethod -Uri "$baseUrl$path" -Method GET -TimeoutSec 5
        Write-Host "[OK] GET $path" -ForegroundColor Green
    } catch {
        Write-Warning "GET $path failed: $_"
    }
}

# POST /work (5 requests)
$payload = @{ cpu_iterations = 50000; delay_ms = 10; payload_size = 10 } | ConvertTo-Json
for ($i = 1; $i -le 5; $i++) {
    try {
        Invoke-RestMethod -Uri "$baseUrl/work" -Method POST -Body $payload -ContentType "application/json" -TimeoutSec 10 | Out-Null
        Write-Host "[OK] POST /work #$i" -ForegroundColor Green
    } catch {
        Write-Warning "POST /work #$i failed: $_"
    }
    Start-Sleep -Milliseconds 300
}

# Optional Locust steady load (30 s, 2 users)
$locustFile = "load_testing\scenarios\demo_locustfile.py"
if (Test-Path $locustFile) {
    Write-Host "Running Locust load (30 s, 2 users)..." -ForegroundColor Yellow
    $env:LOCUST_TARGET = $baseUrl
    Start-Process -FilePath ".venv\Scripts\locust.exe" `
        -ArgumentList "-f", $locustFile, "--headless", "-u", "2", "-r", "1", "--run-time", "30s", "--host", $baseUrl `
        -NoNewWindow -Wait
    Write-Host "[OK] Locust load complete" -ForegroundColor Green
} else {
    Write-Host "[INFO] No demo locustfile found - skipping Locust load." -ForegroundColor Yellow
}

Write-Host "Traffic generation completed." -ForegroundColor Green
