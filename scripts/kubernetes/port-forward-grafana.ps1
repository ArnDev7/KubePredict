param (
    [int]$LocalPort = 3000
)

$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$Namespace = "kubepredict-monitoring"
$ServiceName = "kubepredict-grafana"

Write-Host "=== Starting Port Forward for Grafana Service ==="

# 1. Verify context
$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}

# 2. Verify service exists
kubectl get service $ServiceName -n $Namespace --context $ExpectedContext *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Service '$ServiceName' not found in namespace '$Namespace'."
}

# 3. Check port availability
$occupied = Get-NetTCPConnection -LocalPort $LocalPort -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
if ($occupied) {
    Write-Error "Local port $LocalPort already has an active listener. Please release the port or specify an alternative with -LocalPort (e.g., 13000)."
}

Write-Host "Process ID: $PID"
Write-Host "Binding to: 127.0.0.1:$LocalPort -> $ServiceName:3000 in namespace '$Namespace'..."
Write-Host "To stop forwarding, press Ctrl+C or terminate the process."
kubectl port-forward service/$ServiceName -n $Namespace --address 127.0.0.1 "${LocalPort}:3000" --context $ExpectedContext
