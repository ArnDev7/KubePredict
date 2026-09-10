param (
    [int]$LocalPort = 8000
)

$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$Namespace = "kubepredict-system"
$ServiceName = "kubepredict-workload-api"

Write-Host "=== Starting Port Forward for KubePredict Service ==="

# Verify context
$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}

# Verify service exists
kubectl get service $ServiceName -n $Namespace --context $ExpectedContext *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Service '$ServiceName' not found in namespace '$Namespace'."
}

# Check port availability
$occupied = Get-NetTCPConnection -LocalPort $LocalPort -ErrorAction SilentlyContinue
if ($occupied) {
    Write-Error "Port $LocalPort is already occupied. Please release the port or specify a different -LocalPort."
}

Write-Host "Forwarding localhost:$LocalPort -> $ServiceName:8000 in namespace '$Namespace'..."
Write-Host "Press Ctrl+C to stop port forwarding."
kubectl port-forward service/$ServiceName -n $Namespace "${LocalPort}:8000" --context $ExpectedContext
