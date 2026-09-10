$ErrorActionPreference = "Stop"

$ExpectedContext = "kind-kubepredict-dev"
$Namespace = "kubepredict-system"
$HpaName = "kubepredict-workload-api"
$DeploymentName = "kubepredict-workload-api"
$OutputFile = "docs/evidence/phase-5c/hpa-scale-down.txt"

$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Context mismatch."
}

$demandStopTime = [DateTime]"2026-09-06T10:55:08.7216525+05:30"
$maxObserveSeconds = 420
$endTime = $demandStopTime.AddSeconds($maxObserveSeconds)
$pollIntervalSeconds = 20

$logLines = [System.Collections.Generic.List[string]]::new()

function Log-Output([string]$msg) {
    Write-Host $msg
    $logLines.Add($msg)
}

Log-Output "=== Phase 5C HPA Scale-Down Observation ==="
Log-Output "Demand stop timestamp: $($demandStopTime.ToString('o'))"
Log-Output "Max observation end:   $($endTime.ToString('o')) ($maxObserveSeconds seconds total)"
Log-Output "Polling interval:      $pollIntervalSeconds seconds"
Log-Output ""

$iteration = 0
$scaleDownObserved = $false

while ((Get-Date) -lt $endTime) {
    $iteration++
    $now = Get-Date
    $elapsed = [int]($now - $demandStopTime).TotalSeconds
    $remaining = [int]($endTime - $now).TotalSeconds

    Log-Output "------------------------------------------------------------"
    Log-Output "[Poll $iteration | Time: $($now.ToString('yyyy-MM-ddTHH:mm:ss.fffzzz')) | Elapsed since stop: ${elapsed}s | Remaining: ${remaining}s]"

    $hpaWide = (kubectl get hpa $HpaName -n $Namespace --context $ExpectedContext -o wide | Out-String).TrimEnd()
    Log-Output "--- HPA Status ---"
    Log-Output $hpaWide

    $depWide = (kubectl get deployment $DeploymentName -n $Namespace --context $ExpectedContext -o wide | Out-String).TrimEnd()
    Log-Output "--- Deployment Status ---"
    Log-Output $depWide

    $podWide = (kubectl get pods -n $Namespace --context $ExpectedContext -o wide | Out-String).TrimEnd()
    Log-Output "--- Pods Status ---"
    Log-Output $podWide

    $topPods = (kubectl top pods -n $Namespace --containers --context $ExpectedContext | Out-String).TrimEnd()
    Log-Output "--- Top Pods ---"
    Log-Output $topPods

    $hpaReplicas = kubectl get hpa $HpaName -n $Namespace --context $ExpectedContext -o jsonpath="{.status.currentReplicas}" 2>$null
    $hpaDesired = kubectl get hpa $HpaName -n $Namespace --context $ExpectedContext -o jsonpath="{.status.desiredReplicas}" 2>$null

    Log-Output "Current HPA Replicas: $hpaReplicas | Desired HPA Replicas: $hpaDesired"

    if ($hpaDesired -lt 6) {
        $scaleDownObserved = $true
        Log-Output "`n>>> SCALE-DOWN EVENT OBSERVED! Desired replicas decreased from 6 to $hpaDesired (elapsed: ${elapsed}s) <<<"
    }

    # If scale-down has occurred and pods have scaled down and settled (e.g. to 1 or 2 pods and ready), give an extra 30s to record final event and we can stop
    if ($scaleDownObserved -and $hpaReplicas -le 3 -and $elapsed -ge 340) {
        Log-Output "Scale down completed and stabilized. Stopping observation early."
        break
    }

    $sleepDuration = [math]::Min($pollIntervalSeconds, [math]::Max(1, $remaining))
    Start-Sleep -Seconds $sleepDuration
}

Log-Output "------------------------------------------------------------"
Log-Output "Scale-down observation finished at $(Get-Date -Format o)."
Log-Output "Scale-down observed: $scaleDownObserved"

[System.IO.File]::WriteAllLines($OutputFile, $logLines, [System.Text.Encoding]::UTF8)
Write-Host "Log saved to $OutputFile"
