# KubePredict Demo Commands Cheat-Sheet

> Copy-paste these PowerShell commands in order during recording.
> Run from the repository root: `C:\Users\arnav\Desktop\WebDev\adaptive-predictive-k8s-autoscaler`

---

## 1. Verify cluster health

```powershell
kubectl config current-context
kubectl get nodes
kubectl get pods -A
kubectl get hpa -A --ignore-not-found
kubectl -n kubepredict-system get configmap kubepredict-controller-config -o yaml
```

---

## 2. Run the quality gate (optional, for recording)

```powershell
.venv\Scripts\python.exe -m pytest -q
```

---

## 3. Start the demo environment

```powershell
.\scripts\demo\start-demo.ps1
```

Check which ports were allocated:

```powershell
Get-Content .demo-state\state.json | ConvertFrom-Json
```

---

## 4. Generate dry-run traffic

```powershell
.\scripts\demo\load-demo.ps1
```

---

## 5. Grafana – manual steps (no command needed)

1. Open browser: `http://localhost:3000`
2. Login: `admin` / `admin` (or as configured)
3. Select dashboard: **KubePredict Overview**
4. Time range: **Last 5 minutes**
5. Refresh interval: **5s**
6. Panels to highlight during recording:
   - **Request rate** (`apa_http_requests_total`)
   - **CPU usage** per pod
   - **Replica count** (`kube_deployment_status_replicas_available`)
   - **Prometheus targets up** (`up{job="kubepredict-workload"}`)

> Generate traffic with `load-demo.ps1` **while recording** so live data appears in Grafana.

---

## 6. Check Prometheus targets

```powershell
Invoke-RestMethod http://localhost:9090/api/v1/targets | ConvertTo-Json -Depth 3
```

Or open in browser: `http://localhost:9090/targets`

---

## 7. Check controller metrics

```powershell
$port = (Get-Content .demo-state\state.json | ConvertFrom-Json).metrics.port
Invoke-WebRequest -Uri "http://127.0.0.1:$port/metrics" -UseBasicParsing | Select-Object -ExpandProperty Content
```

---

## 8. Inspect controller decisions (dry-run)

```powershell
kubectl logs deployment/kubepredict-controller -n kubepredict-system --tail=50
```

---

## 9. (Optional) Live-scaling rehearsal

> **Only run this with explicit intent.** The `-ConfirmLiveScalingDemo` flag is required.

```powershell
.\scripts\demo\rehearse-demo.ps1 -ConfirmLiveScalingDemo
```

Watch replicas during the live run:

```powershell
kubectl get deployment kubepredict-workload-api -n kubepredict-system -w
```

---

## 10. Stop the demo environment

```powershell
.\scripts\demo\stop-demo.ps1
```

---

## 11. Final safety verification

```powershell
# Controller mode and live-scaling flag
kubectl -n kubepredict-system get configmap kubepredict-controller-config -o yaml

# Controller replica count
kubectl -n kubepredict-system get deployment kubepredict-controller -o jsonpath="{.status.readyReplicas}"

# Workload replica count
kubectl -n kubepredict-system get deployment kubepredict-workload-api -o jsonpath="{.status.availableReplicas}"

# No live HPA
kubectl get hpa -A --ignore-not-found

# Pods still running
kubectl get pods -n kubepredict-system
kubectl get pods -n kubepredict-monitoring
```

---

## 12. View evidence

```powershell
ls experiments\output\demo-rehearsal\
# Pick the latest run-id and inspect:
Get-Content experiments\output\demo-rehearsal\<run-id>\metadata.json
```

---

*All commands are idempotent and safe to re-run. No commands modify resources outside `kubepredict-system` and `kubepredict-monitoring` namespaces.*

