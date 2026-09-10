param (
    [int]$LocalPort = 18000
)

$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$WorkloadNamespace = "kubepredict-system"
$WorkloadDeployment = "kubepredict-workload-api"
$WorkloadService = "kubepredict-workload-api"
$PythonExe = ".venv\Scripts\python.exe"

Write-Host "=== Starting KubePredict Load Generator Smoke Test ==="

# 1. Verify Python interpreter
if (-not (Test-Path $PythonExe)) {
    Write-Error "Project Python virtual environment interpreter not found at '$PythonExe'."
}

# 2. Verify kubectl context
$currentContext = (kubectl config current-context).Trim()
Write-Host "1. Current Context: $currentContext"
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}

# 3. Verify workload deployment has 2 available replicas
Write-Host "2. Verifying workload deployment available replicas..."
$available = kubectl get deployment $WorkloadDeployment -n $WorkloadNamespace --context $ExpectedContext -o jsonpath="{.status.availableReplicas}" 2>$null
if ($available -ne "2") {
    Write-Error "Workload Deployment '$WorkloadDeployment' does not have 2 available replicas (found: '$available')."
}

# 4. Verify no live HPA exists
Write-Host "3. Verifying no live HPA exists..."
$hpa = kubectl get hpa -A --context $ExpectedContext --ignore-not-found=true
if ($hpa) {
    Write-Error "Live HPA detected. Phase 7A requires fixed 2-replica baseline without HPA."
}

# 5. Verify local port availability
Write-Host "4. Checking local port $LocalPort availability..."
$occupied = Get-NetTCPConnection -LocalPort $LocalPort -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
if ($occupied) {
    Write-Error "Port $LocalPort is already occupied. Choose an alternative with -LocalPort."
}

# 6. Prepare minimal smoke test scenario configuration
$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd_HHmmss")
$runId = "smoke-$timestamp"
$smokeConfigDir = "experiments\output"
if (-not (Test-Path $smokeConfigDir)) {
    New-Item -ItemType Directory -Path $smokeConfigDir -Force | Out-Null
}
$smokeConfigFile = Join-Path $smokeConfigDir "smoke-config.yaml"

$smokeYaml = @"
scenario_name: smoke_test
description: "Minimal smoke test validating Locust runner against deployed workload"
total_duration_seconds: 15
host: "http://127.0.0.1:$LocalPort"
endpoint: "/work"
random_seed: 42
cpu_iterations: 1000
delay_ms: 10
payload_size: 10
request_timeout_seconds: 5.0
stages:
  - duration_seconds: 5
    target_users: 1
    spawn_rate_per_second: 1.0
  - duration_seconds: 10
    target_users: 2
    spawn_rate_per_second: 1.0
"@

Set-Content -Path $smokeConfigFile -Value $smokeYaml -Encoding utf8

$pfProcess = $null
try {
    # 7. Start temporary port-forward
    Write-Host "5. Starting temporary port-forward to $WorkloadService on port $LocalPort..."
    $pfStartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $pfStartInfo.FileName = "kubectl"
    $pfStartInfo.Arguments = "port-forward -n $WorkloadNamespace svc/$WorkloadService ${LocalPort}:8000 --context $ExpectedContext"
    $pfStartInfo.UseShellExecute = $false
    $pfStartInfo.RedirectStandardOutput = $true
    $pfStartInfo.RedirectStandardError = $true
    $pfStartInfo.CreateNoWindow = $true

    $pfProcess = [System.Diagnostics.Process]::Start($pfStartInfo)
    $pfPid = $pfProcess.Id
    Write-Host "Port-forward started with PID $pfPid."

    # Wait for port listener
    $ready = $false
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Milliseconds 500
        $listener = Get-NetTCPConnection -LocalPort $LocalPort -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
        if ($listener) {
            $ready = $true
            break
        }
    }
    if (-not $ready) {
        Write-Error "Port-forward failed to establish listener on 127.0.0.1:$LocalPort within 7.5 seconds."
    }
    Write-Host "Port-forward active on 127.0.0.1:$LocalPort."

    # 8. Execute headless Locust smoke test via runner
    Write-Host "`n6. Executing smoke test via load_testing.runner (Run ID: $runId)..."
    & $PythonExe -m load_testing.runner `
        --scenario $smokeConfigFile `
        --output-root "experiments/output" `
        --run-id $runId `
        --application-image "kubepredict-workload-api:0.1.0" `
        --cluster-name "kubepredict-dev" `
        --controller-mode "fixed"

    $runnerExitCode = $LASTEXITCODE
    if ($runnerExitCode -ne 0) {
        Write-Error "Locust runner smoke test failed with exit code $runnerExitCode."
    }

    Write-Host "`nSmoke test executed successfully."
} finally {
    # 9. Clean up temporary port-forward
    Write-Host "`n7. Cleaning up temporary port-forward..."
    if ($pfProcess -and -not $pfProcess.HasExited) {
        try {
            $pfProcess.Kill()
            $pfProcess.WaitForExit(5000)
            Write-Host "Port-forward process ($($pfProcess.Id)) stopped."
        } catch {
            Write-Warning "Failed to stop port-forward process: $_"
        }
    }

    # Clean up temporary smoke-config.yaml
    if (Test-Path $smokeConfigFile) {
        Remove-Item -Path $smokeConfigFile -Force -ErrorAction SilentlyContinue
    }

    # Confirm port is released
    $rem = Get-NetTCPConnection -LocalPort $LocalPort -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
    if ($rem) {
        Write-Warning "Port $LocalPort is still occupied after cleanup!"
    } else {
        Write-Host "Port $LocalPort confirmed released."
    }
}
