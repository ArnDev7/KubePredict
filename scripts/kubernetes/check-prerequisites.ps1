$ErrorActionPreference = "Stop"

# Refresh PATH if kind is newly installed
if (-not (Get-Command kind -ErrorAction SilentlyContinue)) {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","User") + ";" + [System.Environment]::GetEnvironmentVariable("Path","Machine")
}

Write-Host "=== KubePredict Prerequisites Check ==="

Write-Host "1. Checking Docker client and server..."
docker version
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker daemon is unreachable. Please ensure Docker Desktop is running."
}

Write-Host "`n2. Checking kubectl CLI..."
kubectl version --client
if ($LASTEXITCODE -ne 0) {
    Write-Error "kubectl is not installed or not in PATH."
}

Write-Host "`n3. Checking kind CLI..."
kind version
if ($LASTEXITCODE -ne 0) {
    Write-Error "kind is not installed or not in PATH."
}

Write-Host "`n4. Checking preferred project image (kubepredict-workload-api:0.1.0)..."
$image = docker images -q kubepredict-workload-api:0.1.0
if (-not $image) {
    Write-Warning "Preferred image kubepredict-workload-api:0.1.0 not found. Checking source image..."
    $sourceImage = docker images -q adaptive-predictive-k8s-autoscaler:0.1.0
    if (-not $sourceImage) {
        Write-Error "Neither kubepredict-workload-api:0.1.0 nor adaptive-predictive-k8s-autoscaler:0.1.0 exists."
    } else {
        Write-Host "Source image adaptive-predictive-k8s-autoscaler:0.1.0 exists ($sourceImage)."
    }
} else {
    Write-Host "Preferred image kubepredict-workload-api:0.1.0 exists ($image)."
}

Write-Host "`n5. Checking available system memory..."
$freeMemGB = [math]::Round(((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB), 2)
$totalMemGB = [math]::Round(((Get-CimInstance Win32_OperatingSystem).TotalVisibleMemorySize / 1MB), 2)
Write-Host "Available memory: $freeMemGB GB / Total memory: $totalMemGB GB"

Write-Host "`nAll prerequisite checks completed successfully."
