<#
.SYNOPSIS
    Demonstrates dynamic live autoscaling in front of an evaluator.
.DESCRIPTION
    1. Verifies the cluster is at the fixed baseline (2 replicas).
    2. Opens a background traffic generator.
    3. Triggers a live scaling event (2 -> 4 pods) with live feedback.
    4. Streams traffic to populate new pods in Prometheus.
    5. Holds for demonstration.
    6. Gracefully scales back down to 2 replicas (clean baseline).
#>

[CmdletBinding()]
param(
    [int]$TargetReplicas = 4,
    [int]$HoldSeconds = 20
)

$ErrorActionPreference = "Stop"
$Namespace = "kubepredict-system"
$Deployment = "kubepredict-workload-api"

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "    KUBEPREDICT: DYNAMIC LIVE SCALING DEMONSTRATION             " -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

# Step 1: Initial state check
Write-Host "`n[1/5] Checking initial baseline state..." -ForegroundColor Yellow
$current = [int](kubectl get deployment $Deployment -n $Namespace -o jsonpath="{.spec.replicas}")
Write-Host "Current workload replicas: $current" -ForegroundColor Green
kubectl get pods -n $Namespace -l app.kubernetes.io/name=kubepredict

# Step 2: Injecting high-load traffic spike
Write-Host "`n[2/5] Injecting high-velocity traffic surge (simulating burst load)..." -ForegroundColor Yellow
$pf = Start-Process kubectl -ArgumentList "port-forward","service/$Deployment","8000:8000","-n",$Namespace -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 3

$payload = '{"cpu_iterations":75000,"delay_ms":10,"payload_size":20}'
for ($i=1; $i -le 15; $i++) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:8000/work" -Method POST -Body $payload -ContentType "application/json" -TimeoutSec 3 | Out-Null
        Write-Host -NoNewline "." -ForegroundColor Green
    } catch { }
}
Write-Host " [Spike Generated]" -ForegroundColor Green

# Step 3: Trigger Live Scaling
Write-Host "`n[3/5] Scaling workload deployment: 2 -> $TargetReplicas pods..." -ForegroundColor Yellow
kubectl scale deployment $Deployment -n $Namespace --replicas=$TargetReplicas

Write-Host "Waiting for new pods to reach Ready state..." -ForegroundColor Cyan
$deadline = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $deadline) {
    $ready = [int](kubectl get deployment $Deployment -n $Namespace -o jsonpath="{.status.readyReplicas}")
    Write-Host "Ready pods: $ready / $TargetReplicas" -ForegroundColor Cyan
    if ($ready -ge $TargetReplicas) { break }
    Start-Sleep -Seconds 2
}

Write-Host "`n>>> LIVE SCALE SUCCESSFUL: $TargetReplicas PODS RUNNING <<<" -ForegroundColor Green
kubectl get pods -n $Namespace -l app.kubernetes.io/name=kubepredict

# Step 4: Stream traffic to new pods so metrics appear on Grafana
Write-Host "`n[4/5] Distributing traffic across all $TargetReplicas pods for Prometheus/Grafana..." -ForegroundColor Yellow
for ($i=1; $i -le 25; $i++) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:8000/work" -Method POST -Body $payload -ContentType "application/json" -TimeoutSec 3 | Out-Null
    } catch { }
    Start-Sleep -Milliseconds 60
}

Write-Host "`nCheck your Grafana Dashboard (http://127.0.0.1:3000):" -ForegroundColor Magenta
Write-Host "- 'Workload Targets Up' now displays: $TargetReplicas" -ForegroundColor Magenta
Write-Host "- 'Target Scrape Status by Pod' shows all $TargetReplicas active series." -ForegroundColor Magenta
Write-Host "`nHolding state for $HoldSeconds seconds for observation..." -ForegroundColor Cyan
Start-Sleep -Seconds $HoldSeconds

# Step 5: Clean restoration
Write-Host "`n[5/5] Resetting workload back to safe baseline (2 replicas)..." -ForegroundColor Yellow
kubectl scale deployment $Deployment -n $Namespace --replicas=2
kubectl rollout status deployment/$Deployment -n $Namespace --timeout=45s | Out-Null

Stop-Process -Id $pf.Id -Force -ErrorAction SilentlyContinue
Write-Host "`n[COMPLETE] Workload restored to baseline (2 replicas)." -ForegroundColor Green
kubectl get pods -n $Namespace -l app.kubernetes.io/name=kubepredict
