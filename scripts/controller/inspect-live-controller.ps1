<#
.SYNOPSIS
    Read-only inspection of KubePredict controller status and cluster state (Phase 11).

.DESCRIPTION
    Inspects controller Pod status, ConfigMap mode and live switch, workload replica counts,
    least-privilege RBAC configuration, scaling events, and bounded controller logs.
    Strictly read-only; does not expose sensitive tokens or credentials.
#>
[CmdletBinding()]
param (
    [string]$Context = "kind-kubepredict-dev",
    [string]$Namespace = "kubepredict-system",
    [int]$LogTailLines = 50
)

$ErrorActionPreference = "Continue"

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "KUBEPREDICT CONTROLLER INSPECTION (PHASE 11)" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

# 1. Cluster context
Write-Host "`n--- Cluster Context ---" -ForegroundColor Yellow
$currentContext = kubectl config current-context
Write-Host "Current Context: $currentContext"

# 2. ConfigMap configuration
Write-Host "`n--- Controller Configuration ---" -ForegroundColor Yellow
kubectl --context $Context get configmap kubepredict-controller-config -n $Namespace -o yaml

# 3. Controller Deployment & Pods
Write-Host "`n--- Controller Deployment & Pods ---" -ForegroundColor Yellow
kubectl --context $Context get deployment,pods -l app.kubernetes.io/name=kubepredict-controller -n $Namespace -o wide

# 4. Workload Deployment & Pods
Write-Host "`n--- Workload API Deployment & Replicas ---" -ForegroundColor Yellow
kubectl --context $Context get deployment kubepredict-workload-api -n $Namespace -o wide
kubectl --context $Context get pods -l app.kubernetes.io/name=kubepredict -n $Namespace

# 5. RBAC verification
Write-Host "`n--- RBAC Permissions Audit ---" -ForegroundColor Yellow
$sa = "system:serviceaccount:$Namespace:kubepredict-controller"
$canGetScale = (kubectl --context $Context auth can-i get deployments/scale --as=$sa -n $Namespace).Trim()
$canPatchScale = (kubectl --context $Context auth can-i patch deployments/scale --as=$sa -n $Namespace).Trim()
$canPatchDeployment = (kubectl --context $Context auth can-i patch deployment --as=$sa -n $Namespace).Trim()
$canDeleteDeployment = (kubectl --context $Context auth can-i delete deployment --as=$sa -n $Namespace).Trim()
$canReadSecrets = (kubectl --context $Context auth can-i get secrets --as=$sa -n $Namespace).Trim()
$canModifyHpa = (kubectl --context $Context auth can-i create hpa --as=$sa -n $Namespace).Trim()

Write-Host "  can-i get deployments/scale:    $canGetScale  (expected: yes)"
Write-Host "  can-i patch deployments/scale:  $canPatchScale  (expected: yes)"
Write-Host "  can-i patch deployment:        $canPatchDeployment  (expected: no)"
Write-Host "  can-i delete deployment:       $canDeleteDeployment  (expected: no)"
Write-Host "  can-i get secrets:             $canReadSecrets  (expected: no)"
Write-Host "  can-i create hpa:              $canModifyHpa  (expected: no)"

# 6. Recent Events
Write-Host "`n--- Recent Namespace Events ---" -ForegroundColor Yellow
kubectl --context $Context get events -n $Namespace --sort-by='.metadata.creationTimestamp' | Select-Object -Last 15

# 7. Controller Logs
Write-Host "`n--- Controller Bounded Logs (Last $LogTailLines lines) ---" -ForegroundColor Yellow
kubectl --context $Context logs deployment/kubepredict-controller -n $Namespace --tail=$LogTailLines

Write-Host "`n======================================================================" -ForegroundColor Cyan
Write-Host "Inspection Complete" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

