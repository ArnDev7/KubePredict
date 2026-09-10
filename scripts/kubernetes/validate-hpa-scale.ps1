param (
    [int]$DurationSeconds = 120,
    [int]$Concurrency = 8,
    [int]$CpuIterations = 300000,
    [int]$DelayMs = 0,
    [int]$PayloadSize = 0,
    [int]$LocalPort = 8000,
    [int]$PollIntervalSeconds = 15,
    [switch]$ProbeOnly
)

$ErrorActionPreference = "Stop"

$ExpectedCluster = "kubepredict-dev"
$ExpectedContext = "kind-kubepredict-dev"
$Namespace = "kubepredict-system"
$ServiceName = "kubepredict-workload-api"
$DeploymentName = "kubepredict-workload-api"
$HpaName = "kubepredict-workload-api"
$MetricsNamespace = "kube-system"
$MetricsDeployment = "metrics-server"

# Safety limits aligned with config/settings.py
$MaxCpuIterationsAllowed = 500000
$MaxDelayMsAllowed = 2000

Write-Host "=== KubePredict HPA Functional Scale Validation Utility ==="
Write-Host "Duration: $DurationSeconds s | Concurrency: $Concurrency | CPU Iterations: $CpuIterations | Port: $LocalPort"

# 1. Verify exact context
Write-Host "`n1. Verifying kubectl context..."
$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}

# 2. Verify workload Service and Deployment
Write-Host "2. Verifying workload Service and Deployment..."
kubectl get service $ServiceName -n $Namespace --context $ExpectedContext *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Workload Service '$ServiceName' not found in namespace '$Namespace'."
}
kubectl get deployment $DeploymentName -n $Namespace --context $ExpectedContext *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Workload Deployment '$DeploymentName' not found in namespace '$Namespace'."
}

# 3. Verify HPA exists
Write-Host "3. Verifying HPA '$HpaName' exists..."
kubectl get hpa $HpaName -n $Namespace --context $ExpectedContext *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "HPA '$HpaName' not found in namespace '$Namespace'."
}

# 4. Verify Metrics Server and APIService
Write-Host "4. Verifying Metrics Server..."
$msAvailable = kubectl get deployment $MetricsDeployment -n $MetricsNamespace --context $ExpectedContext -o jsonpath="{.status.availableReplicas}" 2>$null
if ($msAvailable -ne "1") {
    Write-Error "Metrics Server Deployment '$MetricsDeployment' is not available."
}
$apiStatus = kubectl get apiservice v1beta1.metrics.k8s.io --context $ExpectedContext -o jsonpath="{.status.conditions[?(@.type=='Available')].status}" 2>$null
if ($apiStatus -ne "True") {
    Write-Error "APIService v1beta1.metrics.k8s.io is not Available."
}

# 5. Verify HPA current metrics are numeric
Write-Host "5. Verifying HPA current metrics..."
$hpaMetric = kubectl get hpa $HpaName -n $Namespace --context $ExpectedContext -o jsonpath="{.status.currentMetrics[0].resource.current.averageUtilization}" 2>$null
if (-not $hpaMetric -or $hpaMetric -eq "<unknown>") {
    Write-Error "HPA current CPU metric is not resolved (value: '$hpaMetric')."
}
Write-Host "HPA current CPU utilization: $hpaMetric%"

# 6. Verify parameter bounds
if ($CpuIterations -gt $MaxCpuIterationsAllowed) {
    Write-Error "CpuIterations ($CpuIterations) exceeds maximum allowed ($MaxCpuIterationsAllowed)."
}
if ($DelayMs -gt $MaxDelayMsAllowed) {
    Write-Error "DelayMs ($DelayMs) exceeds maximum allowed ($MaxDelayMsAllowed)."
}

# 7. Verify local port availability
$occupied = Get-NetTCPConnection -LocalPort $LocalPort -ErrorAction SilentlyContinue
if ($occupied) {
    Write-Error "Local port $LocalPort is already in use. Release the port or choose an alternate port."
}

$pfProcess = $null
$jobs = @()
$totalCompleted = 0
$totalErrors = 0

try {
    # 8. Start temporary Service port-forward
    Write-Host "`n8. Starting temporary Service port-forward on port $LocalPort..."
    $pfProcess = Start-Process -FilePath "kubectl" -ArgumentList "port-forward service/$ServiceName ${LocalPort}:8000 -n $Namespace --context $ExpectedContext" -PassThru -NoNewWindow
    $pfPid = $pfProcess.Id
    Write-Host "Port-forward process started (PID: $pfPid)."

    # Bounded wait for port-forward to accept connections
    $pfHealthy = $false
    $baseUrl = "http://127.0.0.1:$LocalPort"
    for ($attempt = 1; $attempt -le 15; $attempt++) {
        Start-Sleep -Seconds 1
        if ($pfProcess.HasExited) {
            Write-Error "Port-forward process terminated prematurely with exit code $($pfProcess.ExitCode)."
        }
        try {
            $healthResp = Invoke-RestMethod -Uri "$baseUrl/healthz" -TimeoutSec 2 -ErrorAction Stop
            if ($healthResp.status -eq "healthy") {
                $pfHealthy = $true
                Write-Host "Port-forward healthy and reachable on $baseUrl."
                break
            }
        } catch {
            # Still waiting for port-forward
        }
    }
    if (-not $pfHealthy) {
        Write-Error "Port-forward failed to become healthy within 15 seconds."
    }

    # 9. Low-risk pre-demand probe
    Write-Host "`n9. Executing low-risk pre-demand probe..."
    $probeHealth = Invoke-RestMethod -Uri "$baseUrl/healthz" -TimeoutSec 3
    if ($probeHealth.status -ne "healthy") {
        Write-Error "Pre-demand health probe failed: $($probeHealth | ConvertTo-Json)"
    }
    Write-Host "Pre-demand /healthz probe: SUCCESS (status: $($probeHealth.status))"

    $probeBody = @{
        cpu_iterations = 1000
        delay_ms = 0
        payload_size = 0
    } | ConvertTo-Json

    $probeWork = Invoke-RestMethod -Uri "$baseUrl/work" -Method Post -Body $probeBody -ContentType "application/json" -TimeoutSec 5
    if ($probeWork.status -ne "completed") {
        Write-Error "Pre-demand /work probe failed: $($probeWork | ConvertTo-Json)"
    }
    Write-Host "Pre-demand /work probe: SUCCESS (request_id: $($probeWork.request_id), duration: $($probeWork.duration_ms)ms)"

    # Verify pods and HPA still healthy
    $hpaCheck = kubectl get hpa $HpaName -n $Namespace --context $ExpectedContext -o jsonpath="{.status.currentMetrics[0].resource.current.averageUtilization}" 2>$null
    Write-Host "Post-probe HPA CPU metric: $hpaCheck%"

    $readyPods = (kubectl get pods -n $Namespace --context $ExpectedContext -l app.kubernetes.io/component=workload-api -o jsonpath="{range .items[*]}{.status.conditions[?(@.type=='Ready')].status}{' '}{end}").Trim().Split(" ")
    if ($readyPods.Count -lt 2 -or ($readyPods | Where-Object { $_ -ne "True" }).Count -gt 0) {
        Write-Error "Workload pods not healthy after pre-demand probe."
    }
    Write-Host "Workload pods health verified: $($readyPods.Count) pods Ready."

    if ($ProbeOnly) {
        Write-Host "`nProbeOnly flag specified. Validation probe completed successfully."
        return
    }

    # 10. Start controlled bounded demand generation
    Write-Host "`n10. Starting controlled demand interval..."
    Write-Host "Spawning $Concurrency concurrent worker jobs for $DurationSeconds seconds..."

    $workBody = @{
        cpu_iterations = $CpuIterations
        delay_ms = $DelayMs
        payload_size = $PayloadSize
    } | ConvertTo-Json -Compress

    $startTime = Get-Date
    $stopTime = $startTime.AddSeconds($DurationSeconds)

    Write-Host "Demand start time: $($startTime.ToString('o'))"
    Write-Host "Planned stop time: $($stopTime.ToString('o'))"

    for ($w = 1; $w -le $Concurrency; $w++) {
        $job = Start-Job -ScriptBlock {
            param($Url, $BodyJson, $EndTime)
            Add-Type -AssemblyName System.Net.Http
            $client = [System.Net.Http.HttpClient]::new()
            $client.Timeout = [System.TimeSpan]::FromSeconds(15)
            $completed = 0
            $errors = 0
            while ((Get-Date) -lt $EndTime) {
                try {
                    $content = [System.Net.Http.StringContent]::new($BodyJson, [System.Text.Encoding]::UTF8, "application/json")
                    $response = $client.PostAsync($Url, $content).Result
                    if ($response.IsSuccessStatusCode) {
                        $completed++
                    } else {
                        $errors++
                    }
                    $response.Dispose()
                } catch {
                    $errors++
                }
            }
            $client.Dispose()
            [PSCustomObject]@{
                Completed = $completed
                Errors = $errors
            }
        } -ArgumentList "$baseUrl/work", $workBody, $stopTime
        $jobs += $job
    }

    Write-Host "Launched $($jobs.Count) background worker jobs (Job IDs: $($jobs.Id -join ', '))."

    # 11. Bounded observation loop during demand
    Write-Host "`n11. Monitoring HPA and workload scaling during demand interval..."
    $pollIndex = 0
    while ((Get-Date) -lt $stopTime) {
        $pollIndex++
        $now = Get-Date -Format "yyyy-MM-ddTHH:mm:ss.fffzzz"
        Write-Host "`n[Poll $pollIndex at $now]"

        Write-Host "--- HPA Status ---"
        kubectl get hpa $HpaName -n $Namespace --context $ExpectedContext -o wide

        Write-Host "--- Deployment Status ---"
        kubectl get deployment $DeploymentName -n $Namespace --context $ExpectedContext -o wide

        Write-Host "--- Pods Status ---"
        kubectl get pods -n $Namespace --context $ExpectedContext -o wide

        Write-Host "--- Container CPU & Memory ---"
        kubectl top pods -n $Namespace --containers --context $ExpectedContext

        $remainingSeconds = [math]::Max(0, [int]($stopTime - (Get-Date)).TotalSeconds)
        Write-Host "Remaining demand duration: $remainingSeconds s"

        $sleepTime = [math]::Min($PollIntervalSeconds, $remainingSeconds)
        if ($sleepTime -gt 0) {
            Start-Sleep -Seconds $sleepTime
        }
    }

    Write-Host "`nDemand duration completed. Stopping worker jobs..."
    $demandStopTime = Get-Date
    Write-Host "Demand stop timestamp: $($demandStopTime.ToString('o'))"

    # Wait bounded for all jobs to conclude and harvest counts
    $waitJobResult = Wait-Job -Job $jobs -Timeout 15
    foreach ($j in $jobs) {
        $jobResult = Receive-Job -Job $j -ErrorAction SilentlyContinue
        if ($jobResult) {
            $totalCompleted += [int]$jobResult.Completed
            $totalErrors += [int]$jobResult.Errors
        }
    }

    Write-Host "`n=== Demand Generation Summary ==="
    Write-Host "Total Completed Requests: $totalCompleted"
    Write-Host "Total Request Errors:     $totalErrors"
    Write-Host "Actual Duration:          $(([int]($demandStopTime - $startTime).TotalSeconds)) s"

} finally {
    Write-Host "`n=== Cleanup Phase ==="
    # Clean up worker jobs
    if ($jobs -and $jobs.Count -gt 0) {
        Write-Host "Stopping and removing $($jobs.Count) worker background jobs..."
        foreach ($j in $jobs) {
            try {
                Stop-Job -Job $j -ErrorAction SilentlyContinue
                Remove-Job -Job $j -Force -ErrorAction SilentlyContinue
            } catch {}
        }
    }

    # Clean up port-forward process
    if ($pfProcess -and -not $pfProcess.HasExited) {
        Write-Host "Stopping port-forward process (PID: $($pfProcess.Id))..."
        try {
            Stop-Process -Id $pfProcess.Id -Force -ErrorAction SilentlyContinue
            $pfProcess.WaitForExit(3000)
        } catch {}
    }

    # Verify port is released
    Start-Sleep -Seconds 1
    $portRemaining = Get-NetTCPConnection -LocalPort $LocalPort -ErrorAction SilentlyContinue
    if ($portRemaining) {
        Write-Warning "Local port $LocalPort is still reported as occupied after cleanup."
    } else {
        Write-Host "Local port $LocalPort successfully released."
    }
}
