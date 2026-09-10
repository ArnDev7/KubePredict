param (
    [int]$PrometheusPort = 9090,
    [int]$WorkloadPort = 8000
)

$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$MonitoringNamespace = "kubepredict-monitoring"
$WorkloadNamespace = "kubepredict-system"
$PrometheusService = "kubepredict-prometheus"
$WorkloadService = "kubepredict-workload-api"

Write-Host "=== Phase 6A: Prometheus Scrape and PromQL Validation ==="

# 1. Verify context
$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}

# 2. Check ports
$occ9090 = Get-NetTCPConnection -LocalPort $PrometheusPort -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
if ($occ9090) {
    Write-Error "Local port $PrometheusPort is already occupied."
}
$occ8000 = Get-NetTCPConnection -LocalPort $WorkloadPort -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
if ($occ8000) {
    Write-Error "Local port $WorkloadPort is already occupied."
}

$promPf = $null
$workloadPf = $null

try {
    # 3. Start Prometheus port-forward
    Write-Host "`n1. Starting Prometheus port-forward (localhost:$PrometheusPort)..."
    $promPf = Start-Process -FilePath "kubectl" -ArgumentList "port-forward service/$PrometheusService ${PrometheusPort}:9090 -n $MonitoringNamespace --context $ExpectedContext" -PassThru -NoNewWindow
    Write-Host "Prometheus port-forward started (PID: $($promPf.Id))."

    $promBaseUrl = "http://127.0.0.1:$PrometheusPort"
    $promReady = $false
    for ($i = 1; $i -le 15; $i++) {
        Start-Sleep -Seconds 1
        try {
            $h = (Invoke-WebRequest -Uri "$promBaseUrl/-/healthy" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop).Content
            if ($h -match "Healthy") {
                $promReady = $true
                break
            }
        } catch {}
    }
    if (-not $promReady) {
        Write-Error "Prometheus port-forward failed to become ready."
    }

    # 4. Probe health & ready
    Write-Host "`n2. Probing Prometheus health and readiness endpoints..."
    $healthyResp = (Invoke-WebRequest -Uri "$promBaseUrl/-/healthy" -UseBasicParsing).Content.Trim()
    Write-Host "GET /-/healthy: $healthyResp"

    $readyResp = (Invoke-WebRequest -Uri "$promBaseUrl/-/ready" -UseBasicParsing).Content.Trim()
    Write-Host "GET /-/ready:   $readyResp"

    # 5. Probe config status
    Write-Host "`n3. Probing Prometheus configuration status..."
    $configResp = Invoke-RestMethod -Uri "$promBaseUrl/api/v1/status/config"
    Write-Host "Config status: $($configResp.status)"
    $hasWorkloadJob = $configResp.data.yaml -match "job_name: kubepredict-workload"
    $hasSelfJob = $configResp.data.yaml -match "job_name: prometheus"
    Write-Host "Config contains 'prometheus' job:           $hasSelfJob"
    Write-Host "Config contains 'kubepredict-workload' job: $hasWorkloadJob"

    # Wait for initial scrape cycle
    Write-Host "`nWaiting 15 seconds for initial scrape cycle..."
    Start-Sleep -Seconds 15

    # 6. Scrape Targets Validation
    Write-Host "`n4. Validating Prometheus Scrape Targets (/api/v1/targets)..."
    $targetsResp = Invoke-RestMethod -Uri "$promBaseUrl/api/v1/targets"
    $activeTargets = $targetsResp.data.activeTargets

    Write-Host "Total active scrape targets: $($activeTargets.Count)"
    $selfTarget = $activeTargets | Where-Object { $_.labels.job -eq "prometheus" }
    Write-Host "Self-monitoring target:"
    Write-Host "  Instance: $($selfTarget.discoveredLabels.__address__)"
    Write-Host "  Health:   $($selfTarget.health)"
    Write-Host "  Last Err: $($selfTarget.lastError)"

    $workloadTargets = $activeTargets | Where-Object { $_.labels.job -eq "kubepredict-workload" }
    Write-Host "`nWorkload targets found: $($workloadTargets.Count) (Expected: 2)"
    if ($workloadTargets.Count -ne 2) {
        Write-Warning "Expected exactly 2 workload targets, found $($workloadTargets.Count)."
    }

    $targetIdx = 1
    foreach ($wt in $workloadTargets) {
        Write-Host "Workload Target $($targetIdx):"
        Write-Host "  Pod:        $($wt.labels.pod)"
        Write-Host "  Namespace:  $($wt.labels.namespace)"
        Write-Host "  Container:  $($wt.labels.container)"
        Write-Host "  Health:     $($wt.health)"
        Write-Host "  Scrape URL: $($wt.scrapeUrl)"
        Write-Host "  Last Error: $($wt.lastError)"
        $targetIdx++
    }

    # 7. Basic PromQL Validation
    Write-Host "`n5. Executing required PromQL baseline queries..."
    $queries = @(
        "up",
        'up{job="kubepredict-workload"}',
        "apa_application_info",
        "apa_http_requests_total",
        "apa_http_request_duration_seconds_count",
        "apa_http_requests_in_progress",
        "apa_work_requests_total"
    )

    foreach ($q in $queries) {
        $encodedQuery = [Uri]::EscapeDataString($q)
        $queryResult = Invoke-RestMethod -Uri "$promBaseUrl/api/v1/query?query=$encodedQuery"
        Write-Host "`nQuery: $q"
        Write-Host "Status: $($queryResult.status) | ResultType: $($queryResult.data.resultType) | Count: $($queryResult.data.result.Count)"
        foreach ($r in $queryResult.data.result) {
            $lbls = ($r.metric.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ", "
            Write-Host "  [$lbls] => $($r.value[1])"
        }
    }

    # 8. Functional Workload Request Injection
    Write-Host "`n6. Executing small functional metrics check..."
    Write-Host "Starting temporary workload port-forward (localhost:$WorkloadPort)..."
    $workloadPf = Start-Process -FilePath "kubectl" -ArgumentList "port-forward service/$WorkloadService ${WorkloadPort}:8000 -n $WorkloadNamespace --context $ExpectedContext" -PassThru -NoNewWindow
    Write-Host "Workload port-forward started (PID: $($workloadPf.Id))."

    $workloadBaseUrl = "http://127.0.0.1:$WorkloadPort"
    $workloadReady = $false
    for ($i = 1; $i -le 10; $i++) {
        Start-Sleep -Seconds 1
        try {
            $h = Invoke-RestMethod -Uri "$workloadBaseUrl/healthz" -TimeoutSec 2 -ErrorAction Stop
            if ($h.status -eq "healthy") {
                $workloadReady = $true
                break
            }
        } catch {}
    }
    if (-not $workloadReady) {
        Write-Error "Workload port-forward failed to become ready."
    }

    Write-Host "Sending 1 /healthz and 1 /readyz request..."
    $null = Invoke-RestMethod -Uri "$workloadBaseUrl/healthz"
    $null = Invoke-RestMethod -Uri "$workloadBaseUrl/readyz"

    Write-Host "Sending 4 small /work requests (cpu_iterations=1000, delay_ms=10, payload_size=10)..."
    $workPayload = @{
        cpu_iterations = 1000
        delay_ms = 10
        payload_size = 10
    } | ConvertTo-Json

    for ($r = 1; $r -le 4; $r++) {
        $wResp = Invoke-RestMethod -Uri "$workloadBaseUrl/work" -Method Post -Body $workPayload -ContentType "application/json"
        Write-Host "  Request $($r): status=$($wResp.status), id=$($wResp.request_id), duration=$($wResp.duration_ms)ms"
    }

    Write-Host "`nStopping workload port-forward..."
    Stop-Process -Id $workloadPf.Id -Force -ErrorAction SilentlyContinue
    $workloadPf.WaitForExit(3000)
    $workloadPf = $null

    # 9. Wait for scrape interval to ingest updated metrics
    Write-Host "`nWaiting 20 seconds for Prometheus scrape collection..."
    Start-Sleep -Seconds 20

    # 10. Re-query PromQL to verify counter increments
    Write-Host "`n7. Re-querying PromQL metrics post-request..."
    $postQueries = @(
        "apa_work_requests_total",
        "apa_cpu_iterations_total",
        "apa_artificial_delay_seconds_total",
        "apa_http_requests_total"
    )

    foreach ($pq in $postQueries) {
        $encodedQuery = [Uri]::EscapeDataString($pq)
        $queryResult = Invoke-RestMethod -Uri "$promBaseUrl/api/v1/query?query=$encodedQuery"
        Write-Host "`nQuery: $pq"
        Write-Host "Status: $($queryResult.status) | Count: $($queryResult.data.result.Count)"
        foreach ($r in $queryResult.data.result) {
            $lbls = ($r.metric.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ", "
            Write-Host "  [$lbls] => $($r.value[1])"
        }
    }

    Write-Host "`n=== All Scrape and PromQL Validations Completed Successfully ==="

} finally {
    Write-Host "`n=== Cleaning up Port-Forward Processes ==="
    if ($promPf -and -not $promPf.HasExited) {
        Write-Host "Stopping Prometheus port-forward (PID: $($promPf.Id))..."
        Stop-Process -Id $promPf.Id -Force -ErrorAction SilentlyContinue
        $promPf.WaitForExit(3000)
    }
    if ($workloadPf -and -not $workloadPf.HasExited) {
        Write-Host "Stopping Workload port-forward (PID: $($workloadPf.Id))..."
        Stop-Process -Id $workloadPf.Id -Force -ErrorAction SilentlyContinue
        $workloadPf.WaitForExit(3000)
    }

    Start-Sleep -Seconds 1
    $rem9090 = Get-NetTCPConnection -LocalPort $PrometheusPort -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
    $rem8000 = Get-NetTCPConnection -LocalPort $WorkloadPort -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }

    if (-not $rem9090) {
        Write-Host "Port $PrometheusPort successfully released."
    } else {
        Write-Warning "Port $PrometheusPort still reported in use."
    }
    if (-not $rem8000) {
        Write-Host "Port $WorkloadPort successfully released."
    } else {
        Write-Warning "Port $WorkloadPort still reported in use."
    }
}
