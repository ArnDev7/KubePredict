$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$Namespace = "kubepredict-system"
$HpaName = "kubepredict-workload-api"
$Deployment = "kubepredict-workload-api"

Write-Host "=== Inspecting Kubernetes HPA Baseline State ==="

# Verify context
$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}
Write-Host "Context: $currentContext"

Write-Host "`n1. HPA Resource Summary:"
kubectl get hpa $HpaName -n $Namespace --context $ExpectedContext -o wide --ignore-not-found=true

Write-Host "`n2. Detailed HPA Status & Metrics:"
$hpaJson = kubectl get hpa $HpaName -n $Namespace --context $ExpectedContext -o json 2>$null
if ($hpaJson) {
    $hpa = $hpaJson | ConvertFrom-Json
    Write-Host "  Name:               $($hpa.metadata.name)"
    Write-Host "  Scale Target:       $($hpa.spec.scaleTargetRef.kind)/$($hpa.spec.scaleTargetRef.name)"
    Write-Host "  Min Replicas:       $($hpa.spec.minReplicas)"
    Write-Host "  Max Replicas:       $($hpa.spec.maxReplicas)"
    Write-Host "  Current Replicas:   $($hpa.status.currentReplicas)"
    Write-Host "  Desired Replicas:   $($hpa.status.desiredReplicas)"
    $currUtil = $hpa.status.currentMetrics[0].resource.current.averageUtilization
    if ($null -eq $currUtil) { $currUtil = "<unknown>" } else { $currUtil = "$currUtil%" }
    $tgtUtil = "$($hpa.spec.metrics[0].resource.target.averageUtilization)%"
    Write-Host "  Current CPU Util:   $currUtil"
    Write-Host "  Target CPU Util:    $tgtUtil"
    Write-Host "`n3. HPA Conditions:"
    foreach ($cond in $hpa.status.conditions) {
        Write-Host "  - Type: $($cond.type) | Status: $($cond.status) | Reason: $($cond.reason) | Message: $($cond.message)"
    }
} else {
    Write-Host "  No HPA named '$HpaName' found in namespace '$Namespace'."
}

Write-Host "`n4. Workload Deployment Status:"
kubectl get deployment $Deployment -n $Namespace --context $ExpectedContext -o wide

Write-Host "`n5. Workload Pods Status:"
kubectl get pods -n $Namespace --context $ExpectedContext -l app.kubernetes.io/component=workload-api -o wide

Write-Host "`n6. Pod Resource Metrics (kubectl top pods):"
kubectl top pods -n $Namespace --context $ExpectedContext --containers

Write-Host "`n7. Recent HPA Events:"
kubectl get events -n $Namespace --context $ExpectedContext --field-selector involvedObject.name=$HpaName
