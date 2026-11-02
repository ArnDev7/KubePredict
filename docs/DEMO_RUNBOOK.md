# DEMO Runbook

## Overview
This runbook documents the step‑by‑step procedure for recording the **KubePredict** end‑to‑end demo.  It assumes a fresh `kind` cluster named **kubepredict-dev** and that the repository is checked out on a Windows workstation.

### Prerequisites
- PowerShell 5+ (or PowerShell 7) with `kubectl` in the PATH.
- The `kind` cluster `kubepredict-dev` is running and reachable (`docker ps`).
- Current kubectl context is **kind‑kubepredict‑dev**.
- No live HorizontalPodAutoscaler (HPA) objects exist in the cluster.
- Controller Deployment `kubepredict-controller` is ready with **dry_run** mode and `ENABLE_LIVE_SCALING=false`.
- Workload Deployment `kubepredict-workload-api` has exactly **2** ready replicas.
- Prometheus, Grafana, and Metrics Server are healthy (see verification steps below).

## 1. Verify cluster health
```powershell
kubectl get nodes
kubectl get pods -A
kubectl get hpa -A --ignore-not-found
kubectl -n kubepredict-system get configmap kubepredict-controller-config -o yaml
```
Confirm the output matches the expectations listed in the prerequisites.

## 2. Start the demo environment
```powershell
./scripts/demo/start-demo.ps1
```
The script will:
- Verify the context and workload baseline.
- Start port‑forwards for the workload API (8000), Prometheus (9090), Grafana (3000) and the controller metrics port (9101).
- If any preferred port is already in use, it will pick the next free port and record the actual port in `.demo-state/state.json`.
- Write the state file (`.demo-state/state.json`).

### Expected state file fragment
```json
{
  "api": { "port": 8000, "pid": 12345 },
  "prometheus": { "port": 9090, "pid": 12346 },
  "grafana": { "port": 3000, "pid": 12347 },
  "metrics": { "port": 9101, "pid": 12348 }
}
```

## 3. Generate bounded traffic (dry‑run mode)
```powershell
./scripts/demo/load-demo.ps1
```
The script will:
- Send GET `/`, `/healthz`, `/readyz` to the workload API.
- Send **five** POST `/work` requests with a safe payload `{ "cpu_iterations": 50000, "delay_ms": 10, "payload_size": 10 }`.
- Optionally run a short Locust load test (30 s, 2 users) if `load_testing/scenarios/demo_locustfile.py` exists.

All traffic stays within the demo environment and does not affect any external services.

## 4. (Optional) Live‑scaling demonstration
Only run this step if you explicitly want to showcase live scaling.  Provide the `-ConfirmLiveScalingDemo` switch when running the rehearsal script.
```powershell
./scripts/demo/rehearse-demo.ps1 -ConfirmLiveScalingDemo
```
The script will:
1. Verify the same pre‑conditions as above.
2. Enable live‑validation (`./scripts/controller/enable-live-validation.ps1`).
3. Run a Locust load for up to **90 seconds** with **4 users** (max `cpu_iterations` 100 000).
4. Watch for the first scale‑up (replica count > 2).  As soon as a scale‑up is observed, the script stops the load.
5. Immediately disable live‑scaling (`./scripts/controller/disable-live-scaling.ps1`).
6. Re‑apply the base manifests (`kubernetes/base`) to restore the workload to **2** replicas.
7. Collect controller logs and write a `metadata.json` file indicating whether live scaling succeeded.

## 5. Stop the demo environment
```powershell
./scripts/demo/stop-demo.ps1
```
This script reads `.demo-state/state.json`, terminates all port‑forward processes, and removes the `.demo-state` directory.

## 6. Verify final safe state
```powershell
kubectl -n kubepredict-system get configmap kubepredict-controller-config -o yaml
kubectl get deployment kubepredict-controller -n kubepredict-system -o jsonpath="{.status.readyReplicas}"
kubectl get deployment kubepredict-workload-api -n kubepredict-system -o jsonpath="{.status.availableReplicas}"
kubectl get hpa -A --ignore-not-found
```
All values must match the **Required final state** (dry_run, 2 workload replicas, no HPA, etc.).

## 7. Evidence collection
All logs and metadata are stored under:
```
experiments/output/demo-rehearsal/<timestamp>/
```
The directory contains:
- `controller.log` – full controller output during the run.
- `metadata.json` – flags describing the run (`demo_rehearsal`, `formal_experiment`, `live_scaling_enabled`, `live_scaling_success`).

These directories are ignored by Git via `.gitignore`.

---
**End of Runbook**

