param (
    [int]$LocalPort = 9090
)

$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$Namespace = "kubepredict-monitoring"
$ServiceName = "kubepredict-prometheus"

Write-Host "=== Starting Port Forward for Prometheus Service ==="

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
    Write-Error "Local port $LocalPort already has an active listener. Please release the port or specify an alternative with -LocalPort (e.g., 19090)."
}

Write-Host "Forwarding localhost:$LocalPort -> $ServiceName:9090 in namespace '$Namespace'..."
Write-Host "To stop forwarding, press Ctrl+C or terminate the process."
kubectl port-forward service/$ServiceName -n $Namespace "${LocalPort}:9090" --context $ExpectedContext
