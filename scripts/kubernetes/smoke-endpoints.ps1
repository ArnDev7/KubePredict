param(
    [int]$LocalPort = 8000
)

$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$Namespace = "kubepredict-system"
$ServiceName = "kubepredict-workload-api"

$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Context mismatch: expected '$ExpectedContext', found '$currentContext'."
}

# Ensure port is not occupied
$occ = Get-NetTCPConnection -LocalPort $LocalPort -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
if ($occ) {
    Write-Error "Port $LocalPort already has an active listener."
}

Write-Host "Starting temporary port-forward on port $LocalPort..."
$pf = Start-Process -FilePath "kubectl" -ArgumentList "port-forward service/$ServiceName ${LocalPort}:8000 -n $Namespace --context $ExpectedContext" -PassThru -NoNewWindow
$baseUrl = "http://127.0.0.1:$LocalPort"

try {
    # Wait for readiness
    $ready = $false
    for ($i = 1; $i -le 10; $i++) {
        Start-Sleep -Seconds 1
        try {
            $h = Invoke-RestMethod -Uri "$baseUrl/healthz" -TimeoutSec 2
            if ($h.status -eq "healthy") {
                $ready = $true
                break
            }
        } catch {}
    }
    if (-not $ready) {
        Write-Error "Port forward failed to become ready."
    }

    Write-Host "1. /healthz:"
    $health = Invoke-RestMethod -Uri "$baseUrl/healthz" -TimeoutSec 3
    Write-Host "   status: $($health.status)"

    Write-Host "2. /readyz:"
    $readyz = Invoke-RestMethod -Uri "$baseUrl/readyz" -TimeoutSec 3
    Write-Host "   status: $($readyz.status)"

    Write-Host "3. /work:"
    $body = @{ cpu_iterations = 1000; delay_ms = 0; payload_size = 0 } | ConvertTo-Json
    $work = Invoke-RestMethod -Uri "$baseUrl/work" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 5
    Write-Host "   status: $($work.status) | request_id: $($work.request_id) | duration: $($work.duration_ms)ms"

    Write-Host "4. /metrics:"
    $metrics = (Invoke-WebRequest -Uri "$baseUrl/metrics" -TimeoutSec 3).Content
    $hasCounter = $metrics.Contains("work_requests_total")
    Write-Host "   metrics exposed: $hasCounter"

} finally {
    if ($pf -and -not $pf.HasExited) {
        Write-Host "Stopping port-forward (PID: $($pf.Id))..."
        Stop-Process -Id $pf.Id -Force -ErrorAction SilentlyContinue
        $pf.WaitForExit(3000)
    }
    Start-Sleep -Seconds 1
    $listening = Get-NetTCPConnection -LocalPort $LocalPort -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
    if ($listening) {
        Write-Warning "Port $LocalPort still listening."
    } else {
        Write-Host "Port $LocalPort released successfully."
    }
}
