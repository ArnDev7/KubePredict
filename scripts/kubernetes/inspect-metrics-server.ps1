param (
    [switch]$IncludeLogs
)

$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$Namespace = "kube-system"
$WorkloadNamespace = "kubepredict-system"

Write-Host "=== Inspecting Kubernetes Metrics Server State ==="

# Verify context
$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}

Write-Host "`n1. Metrics Server Deployment:"
kubectl get deployment metrics-server -n $Namespace --context $ExpectedContext -o wide

Write-Host "`n2. Metrics Server Pods:"
kubectl get pods -n $Namespace --context $ExpectedContext -l k8s-app=metrics-server -o wide

Write-Host "`n3. Metrics Server Container Details (Image and Arguments):"
kubectl get pods -n $Namespace --context $ExpectedContext -l k8s-app=metrics-server -o jsonpath="{range .items[*]}Pod: {.metadata.name}{'\n'}Image: {.spec.containers[0].image}{'\n'}Args: {.spec.containers[0].args}{'\n'}Restarts: {.status.containerStatuses[0].restartCount}{'\n'}{end}"

Write-Host "`n4. APIService v1beta1.metrics.k8s.io Status:"
kubectl get apiservice v1beta1.metrics.k8s.io --context $ExpectedContext -o wide

Write-Host "`n5. Node Resource Usage (kubectl top nodes):"
kubectl top nodes --context $ExpectedContext

Write-Host "`n6. Workload Pod Resource Usage (kubectl top pods):"
kubectl top pods -n $WorkloadNamespace --context $ExpectedContext --containers

Write-Host "`n7. Recent Metrics Server Events:"
kubectl get events -n $Namespace --context $ExpectedContext --field-selector involvedObject.name=metrics-server

if ($IncludeLogs) {
    Write-Host "`n8. Recent Metrics Server Logs (tail 50):"
    kubectl logs -n $Namespace --context $ExpectedContext -l k8s-app=metrics-server --tail=50
}
