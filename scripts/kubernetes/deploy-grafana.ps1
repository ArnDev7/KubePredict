param (
    [string]$AdminPassword = ""
)

$ErrorActionPreference = "Stop"

$ExpectedCluster = "kubepredict-dev"
$ExpectedContext = "kind-kubepredict-dev"
$ManifestDir = "kubernetes/monitoring/grafana"
$MonitoringNamespace = "kubepredict-monitoring"
$GrafanaDeployment = "kubepredict-grafana"
$GrafanaService = "kubepredict-grafana"
$SecretName = "grafana-admin-credentials"
$PrometheusDeployment = "kubepredict-prometheus"
$PrometheusService = "kubepredict-prometheus"
$WorkloadNamespace = "kubepredict-system"
$WorkloadDeployment = "kubepredict-workload-api"
$DashboardJsonPath = "monitoring/grafana/dashboards/kubepredict-overview.json"

Write-Host "=== Deploying KubePredict Grafana Dashboard Stack ==="

# 1. Verify Docker daemon
Write-Host "1. Verifying Docker daemon..."
docker version *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker daemon is unreachable. Please ensure Docker Desktop is running."
}

# 2. Verify kind CLI
Write-Host "2. Verifying kind CLI..."
kind version *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "kind CLI is not available in PATH."
}

# 3. Verify cluster exists
Write-Host "3. Verifying kind cluster '$ExpectedCluster'..."
$clusters = kind get clusters
if ($clusters -notcontains $ExpectedCluster) {
    Write-Error "Kind cluster '$ExpectedCluster' not found."
}

# 4. Verify current context
Write-Host "4. Verifying kubectl context..."
$currentContext = (kubectl config current-context).Trim()
if ($currentContext -ne $ExpectedContext) {
    Write-Error "Current context '$currentContext' does not match expected context '$ExpectedContext'."
}

# 5. Verify all nodes are Ready
Write-Host "5. Verifying cluster nodes are Ready..."
kubectl wait --for=condition=Ready nodes --all --timeout=60s --context $ExpectedContext
if ($LASTEXITCODE -ne 0) {
    Write-Error "Cluster nodes are not in Ready state."
}

# 6. Verify Prometheus is Ready
Write-Host "6. Verifying Prometheus Deployment and Service..."
$promAvailable = kubectl get deployment $PrometheusDeployment -n $MonitoringNamespace --context $ExpectedContext -o jsonpath="{.status.availableReplicas}" 2>$null
if ($promAvailable -ne "1") {
    Write-Error "Prometheus Deployment '$PrometheusDeployment' is not available in '$MonitoringNamespace'."
}
$promEp = kubectl get endpointslice -n $MonitoringNamespace -l "kubernetes.io/service-name=$PrometheusService" --context $ExpectedContext -o jsonpath="{.items[*].endpoints[*].addresses[*]}" 2>$null
if (-not $promEp) {
    $promEp = (kubectl get endpoints $PrometheusService -n $MonitoringNamespace --context $ExpectedContext -o jsonpath="{.subsets[*].addresses[*].ip}" 2>&1 | Where-Object { $_ -notmatch "Warning" })
}
if (-not $promEp) {
    Write-Error "Prometheus Service '$PrometheusService' has no ready endpoints."
}
Write-Host "Prometheus endpoint active at: $promEp"

# 7. Verify fixed workload Deployment and targets
Write-Host "7. Verifying fixed workload Deployment '$WorkloadDeployment'..."
$workloadReplicas = kubectl get deployment $WorkloadDeployment -n $WorkloadNamespace --context $ExpectedContext -o jsonpath="{.status.availableReplicas}" 2>$null
if ($workloadReplicas -ne "2") {
    Write-Error "Workload Deployment '$WorkloadDeployment' does not have 2 available replicas."
}

# 8. Validate dashboard JSON
Write-Host "8. Validating dashboard JSON at '$DashboardJsonPath'..."
if (-not (Test-Path $DashboardJsonPath)) {
    Write-Error "Canonical dashboard JSON file '$DashboardJsonPath' not found."
}
try {
    $rawJson = Get-Content -Raw -Path $DashboardJsonPath -Encoding utf8
    $null = ConvertFrom-Json $rawJson
    Write-Host "Dashboard JSON parsed successfully."
} catch {
    Write-Error "Failed to parse dashboard JSON: $_"
}

# 9. Render Grafana Kustomization manifests
Write-Host "9. Rendering Grafana Kustomize manifests from '$ManifestDir'..."
$rendered = kubectl kustomize $ManifestDir
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to render Kustomize manifests from '$ManifestDir'."
}
$resourceCount = ($rendered | Select-String -Pattern "^apiVersion:").Count
Write-Host "Successfully rendered $resourceCount resources from '$ManifestDir'."

# 10. Server-side dry-run verification
Write-Host "10. Running server-side dry-run verification..."
kubectl apply --dry-run=server -k $ManifestDir --context $ExpectedContext
if ($LASTEXITCODE -ne 0) {
    Write-Error "Server dry-run failed for Grafana manifests."
}

# 11. Handle Grafana administrator password securely
Write-Host "11. Preparing Grafana administrator credentials..."
$ObviousDefaults = @("admin", "password", "changeme", "grafana", "kubepredict", "root", "123456", "12345678")

if ([string]::IsNullOrWhiteSpace($AdminPassword)) {
    if (-not [string]::IsNullOrWhiteSpace($env:GRAFANA_ADMIN_PASSWORD)) {
        $AdminPassword = $env:GRAFANA_ADMIN_PASSWORD
    } else {
        # Securely generate random alphanumeric password
        $bytes = New-Object byte[] 24
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $AdminPassword = [Convert]::ToBase64String($bytes) -replace "[+/=]", ""
        if ($AdminPassword.Length -lt 16) {
            $AdminPassword = $AdminPassword + "KubePred9!"
        }
    }
}

if ($ObviousDefaults -contains $AdminPassword.ToLower()) {
    Write-Error "Specified administrator password is an obvious default and is rejected for security reasons."
}

if ($AdminPassword.Length -lt 8) {
    Write-Error "Administrator password must be at least 8 characters long."
}

# 12. Create or update Secret without writing to disk or echoing
Write-Host "12. Creating or updating runtime Secret '$SecretName' in '$MonitoringNamespace'..."
kubectl create secret generic $SecretName `
    --namespace=$MonitoringNamespace `
    --from-literal=admin-user=admin `
    --from-literal=admin-password=$AdminPassword `
    --dry-run=client -o yaml | kubectl apply -f - --context $ExpectedContext
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to apply runtime Secret '$SecretName'."
}
Write-Host "Secret '$SecretName' applied successfully (credentials held safely in cluster Secret)."

# 13. Apply Grafana Kustomization
Write-Host "13. Applying Grafana manifests to cluster..."
kubectl apply -k $ManifestDir --context $ExpectedContext
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to apply Grafana manifests."
}

# 14. Wait for Grafana rollout
Write-Host "14. Waiting for Grafana Deployment rollout (bounded 240s timeout)..."
kubectl rollout status deployment/$GrafanaDeployment -n $MonitoringNamespace --context $ExpectedContext --timeout=240s
if ($LASTEXITCODE -ne 0) {
    Write-Error "Grafana Deployment rollout failed."
}

$grafanaEp = kubectl get endpointslice -n $MonitoringNamespace -l "kubernetes.io/service-name=$GrafanaService" --context $ExpectedContext -o jsonpath="{.items[*].endpoints[*].addresses[*]}" 2>$null
if (-not $grafanaEp) {
    $grafanaEp = (kubectl get endpoints $GrafanaService -n $MonitoringNamespace --context $ExpectedContext -o jsonpath="{.subsets[*].addresses[*].ip}" 2>&1 | Where-Object { $_ -notmatch "Warning" })
}
if (-not $grafanaEp) {
    Write-Error "Grafana Service '$GrafanaService' has no ready endpoints."
}
Write-Host "Grafana endpoint active at: $grafanaEp"

Write-Host "`nGrafana deployment completed successfully."
