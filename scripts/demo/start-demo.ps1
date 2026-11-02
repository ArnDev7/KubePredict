<#
.SYNOPSIS
    Starts the KubePredict demo environment.
.DESCRIPTION
    Verifies the Kubernetes context, launches required port-forwards,
    and records actual ports and process IDs in .demo-state/state.json.
    If a preferred port is already in use, selects the next free port.
#>

$ErrorActionPreference = "Stop"

$ExpectedContext    = "kind-kubepredict-dev"
$WorkloadNamespace  = "kubepredict-system"
$MonitoringNamespace = "kubepredict-monitoring"

$StateDir  = ".demo-state"
$StateFile = Join-Path $StateDir "state.json"

# Preferred ports
$PortPreferred = @{
    api        = 8000
    prometheus = 9090
    grafana    = 3000
    metrics    = 9101
}

# Services to forward  (namespace, service name, target port)
$Services = @{
    api        = @{ ns = $WorkloadNamespace;   svc = "kubepredict-workload-api";       targetPort = 8000 }
    prometheus = @{ ns = $MonitoringNamespace; svc = "kubepredict-prometheus";         targetPort = 9090 }
    grafana    = @{ ns = $MonitoringNamespace; svc = "kubepredict-grafana";           targetPort = 3000 }
    metrics    = @{ ns = $WorkloadNamespace;   svc = "kubepredict-controller";         targetPort = 9101 }
}

function Get-FreePort([int]$preferred) {
    $port = $preferred
    while (Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue) {
        $port++
    }
    return $port
}

# ---------- Prerequisite checks ----------
$current = (kubectl config current-context).Trim()
if ($current -ne $ExpectedContext) {
    throw "Current context is '$current', expected '$ExpectedContext'."
}
Write-Host "[PASS] Context: $current" -ForegroundColor Green

$deploy = kubectl get deployment kubepredict-workload-api -n $WorkloadNamespace -o json | ConvertFrom-Json
if ($deploy.spec.replicas -ne 2 -or $deploy.status.availableReplicas -ne 2) {
    throw "Workload not at 2 replicas (desired=$($deploy.spec.replicas), available=$($deploy.status.availableReplicas))."
}
Write-Host "[PASS] Workload at 2 replicas" -ForegroundColor Green

$hpa = kubectl get hpa -A --ignore-not-found
if ($hpa) { throw "Live HPA detected - must be absent for demo." }
Write-Host "[PASS] No live HPA" -ForegroundColor Green

# ---------- Create state directory ----------
if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir | Out-Null }

# ---------- Launch port-forwards ----------
$state = @{}

foreach ($key in $Services.Keys) {
    $svcInfo   = $Services[$key]
    $preferred = $PortPreferred[$key]
    $actual    = Get-FreePort $preferred

    if ($actual -ne $preferred) {
        Write-Warning "Port $preferred busy; using $actual for $key"
    }

    $localTarget = "${actual}:$($svcInfo.targetPort)"
    Write-Host "Port-forward $key : $localTarget -> $($svcInfo.svc) ($($svcInfo.ns))" -ForegroundColor Cyan

    $pf = Start-Process -FilePath "kubectl" `
        -ArgumentList "port-forward", "service/$($svcInfo.svc)", $localTarget, "-n", $svcInfo.ns, "--context", $ExpectedContext `
        -PassThru -WindowStyle Hidden

    $state[$key] = @{ port = $actual; pid = $pf.Id }
    Start-Sleep -Seconds 1
}

# ---------- Persist state ----------
$state | ConvertTo-Json -Depth 5 | Set-Content -Path $StateFile -Encoding utf8
Write-Host "State written to $StateFile" -ForegroundColor Green
Write-Host "Demo environment started. Run '.\scripts\demo\load-demo.ps1' to generate traffic." -ForegroundColor Green
