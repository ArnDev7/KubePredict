$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$Namespace = "kubepredict-system"

Write-Host "=== Inspecting Fixed-Replica Baseline Resources ==="

# Verify context
$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}

Write-Host "`n1. Namespace:"
kubectl get namespace $Namespace --context $ExpectedContext -o wide

Write-Host "`n2. ConfigMap metadata:"
kubectl get configmap -n $Namespace --context $ExpectedContext

Write-Host "`n3. Deployment:"
kubectl get deployment -n $Namespace --context $ExpectedContext -o wide

Write-Host "`n4. ReplicaSets:"
kubectl get replicaset -n $Namespace --context $ExpectedContext -o wide

Write-Host "`n5. Pods:"
kubectl get pods -n $Namespace --context $ExpectedContext -o wide

Write-Host "`n6. Service:"
kubectl get service -n $Namespace --context $ExpectedContext -o wide

Write-Host "`n7. Events:"
kubectl get events -n $Namespace --context $ExpectedContext --sort-by=.metadata.creationTimestamp
