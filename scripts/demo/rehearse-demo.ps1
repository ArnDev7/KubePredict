<#
.SYNOPSIS
    Runs the full KubePredict demo rehearsal (dry-run by default).
.DESCRIPTION
    1. Verifies cluster pre-conditions.
    2. Starts demo environment (port-forwards).
    3. Generates bounded dry-run traffic via load-demo.ps1.
    4. Captures controller logs as evidence.
    5. Optionally enables live scaling for one observed scale-up (-ConfirmLiveScalingDemo).
    6. Restores safe baseline and stops the demo environment.
    Evidence is stored under experiments/output/demo-rehearsal/<timestamp>/.
.PARAMETER ConfirmLiveScalingDemo
    Must be supplied explicitly to enable the optional live-scaling portion.
#>

param(
    [switch]$ConfirmLiveScalingDemo
)

$ErrorActionPreference = "Stop"

$ExpectedContext   = "kind-kubepredict-dev"
$WorkloadNamespace = "kubepredict-system"
$ScriptDir         = $PSScriptRoot

function Assert-Eq([object]$actual, [object]$expected, [string]$label) {
    if ("$actual" -ne "$expected") {
        throw "${label}: expected '$expected', got '$actual'"
    }
}

Write-Host "=== KubePredict Demo Rehearsal ===" -ForegroundColor Cyan

# ---------- Pre-condition checks ----------
$ctx = (kubectl config current-context).Trim()
Assert-Eq $ctx $ExpectedContext "Kubectl context"
Write-Host "[PASS] Context: $ctx" -ForegroundColor Green

$hpa = kubectl get hpa -A --ignore-not-found
if ($hpa) { throw "Live HPA detected - must be absent for demo." }
Write-Host "[PASS] No live HPA" -ForegroundColor Green

$ctrl = kubectl get deployment kubepredict-controller -n $WorkloadNamespace -o json | ConvertFrom-Json
Assert-Eq $ctrl.status.readyReplicas 1 "Controller ready replicas"
Write-Host "[PASS] Controller ready" -ForegroundColor Green

$mode = (kubectl get configmap kubepredict-controller-config -n $WorkloadNamespace -o jsonpath="{.data.CONTROLLER_MODE}").Trim()
Assert-Eq $mode "dry_run" "Controller mode"
Write-Host "[PASS] Controller mode: $mode" -ForegroundColor Green

$liveFlag = (kubectl get configmap kubepredict-controller-config -n $WorkloadNamespace -o jsonpath="{.data.ENABLE_LIVE_SCALING}").Trim()
Assert-Eq $liveFlag "false" "ENABLE_LIVE_SCALING"
Write-Host "[PASS] ENABLE_LIVE_SCALING: $liveFlag" -ForegroundColor Green

$wl = kubectl get deployment kubepredict-workload-api -n $WorkloadNamespace -o json | ConvertFrom-Json
if ($wl.spec.replicas -ne 2 -or $wl.status.availableReplicas -ne 2) {
    throw "Workload not at 2 replicas (desired=$($wl.spec.replicas), available=$($wl.status.availableReplicas))"
}
Write-Host "[PASS] Workload at 2 replicas" -ForegroundColor Green

# ---------- Setup evidence directory ----------
$runId      = (Get-Date -Format "yyyyMMdd_HHmmss")
$evidenceDir = Join-Path "experiments\output\demo-rehearsal" $runId
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null
Write-Host "Evidence directory: $evidenceDir" -ForegroundColor Cyan

# ---------- Start demo environment ----------
Write-Host "`nStarting demo environment..." -ForegroundColor Cyan
& (Join-Path $ScriptDir "start-demo.ps1")

$liveScalingSuccess = $false

try {
    # ---------- Generate dry-run traffic ----------
    Write-Host "`nGenerating dry-run traffic..." -ForegroundColor Cyan
    & (Join-Path $ScriptDir "load-demo.ps1")

    # Capture controller logs
    kubectl logs deployment/kubepredict-controller -n $WorkloadNamespace 2>&1 |
        Set-Content -Path (Join-Path $evidenceDir "controller.log") -Encoding utf8
    Write-Host "[OK] Controller logs captured." -ForegroundColor Green

    # ---------- Optional live-scaling ----------
    if ($ConfirmLiveScalingDemo) {
        Write-Host "`n=== LIVE SCALING DEMO (confirmed) ===" -ForegroundColor Yellow

        # Enable live validation
        & (Join-Path $PSScriptRoot "..\controller\enable-live-validation.ps1")
        Start-Sleep -Seconds 5

        # Generate load (90 s, 4 users, max 100k iterations)
        $locustFile = "load_testing\scenarios\demo_locustfile.py"
        if (Test-Path $locustFile) {
            $apiPort = (Get-Content ".demo-state\state.json" -Raw | ConvertFrom-Json).api.port
            Write-Host "Locust load: 90 s, 4 users, host http://127.0.0.1:$apiPort" -ForegroundColor Yellow
            $locustProc = Start-Process -FilePath ".venv\Scripts\locust.exe" `
                -ArgumentList "-f", $locustFile, "--headless", "-u", "4", "-r", "1", "--run-time", "90s", "--host", "http://127.0.0.1:$apiPort" `
                -PassThru -NoNewWindow

            # Monitor for first scale-up (up to 100 s)
            $deadline = (Get-Date).AddSeconds(100)
            while ((Get-Date) -lt $deadline) {
                $rep = [int](kubectl get deployment kubepredict-workload-api -n $WorkloadNamespace -o jsonpath="{.status.availableReplicas}")
                if ($rep -gt 2) {
                    $liveScalingSuccess = $true
                    Write-Host "[OK] Scale-up observed: $rep replicas" -ForegroundColor Green
                    break
                }
                Start-Sleep -Seconds 3
            }

            # Stop load generator
            Stop-Process -Id $locustProc.Id -Force -ErrorAction SilentlyContinue
        } else {
            Write-Warning "No demo locustfile found; skipping Locust load during live scaling."
        }

        # Restore dry-run mode immediately
        Write-Host "Restoring dry-run mode..." -ForegroundColor Yellow
        & (Join-Path $PSScriptRoot "..\controller\disable-live-scaling.ps1")

        # Re-apply base manifests (reset workload to 2 replicas)
        kubectl apply -k kubernetes\base --context $ExpectedContext 2>&1 | Out-Null
        kubectl rollout status deployment/kubepredict-workload-api -n $WorkloadNamespace --timeout=60s

        # Verify restored
        $wlAfter = kubectl get deployment kubepredict-workload-api -n $WorkloadNamespace -o json | ConvertFrom-Json
        if ($wlAfter.status.availableReplicas -ne 2) {
            Write-Warning "Workload replicas after restore: $($wlAfter.status.availableReplicas) (expected 2)"
        } else {
            Write-Host "[OK] Workload restored to 2 replicas" -ForegroundColor Green
        }

        $hpaAfter = kubectl get hpa -A --ignore-not-found
        if ($hpaAfter) {
            Write-Warning "HPA still present after restore!"
        } else {
            Write-Host "[OK] No live HPA" -ForegroundColor Green
        }
    }

} finally {
    # ---------- Stop demo environment ----------
    Write-Host "`nStopping demo environment..." -ForegroundColor Cyan
    & (Join-Path $ScriptDir "stop-demo.ps1")
}

# ---------- Write metadata ----------
$metadata = [ordered]@{
    demo_rehearsal         = $true
    formal_experiment      = $false
    run_id                 = $runId
    live_scaling_enabled   = $ConfirmLiveScalingDemo.IsPresent
    live_scaling_success   = $liveScalingSuccess
    timestamp              = (Get-Date -Format "o")
}
$metadata | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $evidenceDir "metadata.json") -Encoding utf8

Write-Host "`n=== Demo rehearsal complete ===" -ForegroundColor Green
Write-Host "Evidence: $evidenceDir" -ForegroundColor Cyan
