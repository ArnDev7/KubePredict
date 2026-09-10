# Grafana Observability and KubePredict Dashboard

This document details the deployment, configuration, provisioning, and operational architecture of Grafana in the KubePredict testbed (`Phase 6B`).

---

## 1. Overview and Architecture

Grafana is deployed within the project-owned `kubepredict-monitoring` namespace on the local `kubepredict-dev` kind cluster. It acts as the visualization and operational dashboard layer for the KubePredict cloud-native workload and autoscaling research testbed.

Grafana is provisioned declaratively via Kubernetes ConfigMaps ("dashboard-as-code" and "datasource-as-code"), ensuring that no manual UI configuration is required and that the dashboard definition remains version-controlled in the repository.

```
+-------------------------------------------------------------------------------+
| kubepredict-monitoring namespace                                              |
|                                                                               |
|   +-----------------------+                    +--------------------------+   |
|   |  kubepredict-grafana  |  HTTP Proxy (9090) |  kubepredict-prometheus  |   |
|   |      (Port 3000)      |------------------->|       (Port 9090)        |   |
|   +-----------------------+                    +--------------------------+   |
|               ^                                              ^                |
|               | localhost:3000 port-forward                  | Scrapes        |
+---------------|----------------------------------------------|----------------+
                |                                              |
     Developer Localhost                                kubepredict-system
                                                    (kubepredict-workload-api)
```

---

## 2. Image Selection and Specifications

- **Image Repository**: `docker.io/grafana/grafana`
- **Selected Version**: `11.2.0` (tag `grafana/grafana:11.2.0`)
- **Image Digest**: `sha256:408afb9726de5122b00a2576763a8a57a3c86d5b0eff5305bc994ceb3eb96c3f`
- **Selection Rationale**: `11.2.0` is an official, stable, community-supported OSS release of Grafana. Floating tags (`latest`, `main`, `master`) are strictly prohibited to ensure build repeatability and auditability.
- **Enterprise Status**: Grafana OSS is used; enterprise features are not enabled or required.

---

## 3. Workload and Resource Specifications

- **Namespace**: `kubepredict-monitoring`
- **Deployment Name**: `kubepredict-grafana`
- **Desired Replicas**: `1`
- **Container Name**: `grafana`
- **Service Name**: `kubepredict-grafana`
- **Service Type**: `ClusterIP` (No NodePort, LoadBalancer, or Ingress)
- **Port**: `3000` (targetPort `http` / 3000)
- **Compute Resources**:
  - `requests.cpu`: `100m`
  - `requests.memory`: `128Mi`
  - `limits.cpu`: `500m`
  - `limits.memory`: `384Mi`

---

## 4. Credential Strategy and Runtime Secret

To uphold security standards without committing sensitive credentials into Git:
- **No Hardcoded Passwords**: Neither `admin/admin` nor any default credentials are used.
- **Runtime Secret Generation**: Administrator credentials reside in a Kubernetes Secret named `grafana-admin-credentials` in `kubepredict-monitoring`.
- **Generation via Script**: `scripts/kubernetes/deploy-grafana.ps1` either accepts an explicit `$AdminPassword` parameter, checks `$env:GRAFANA_ADMIN_PASSWORD`, or cryptographically generates a secure 24-character random password using `[System.Security.Cryptography.RandomNumberGenerator]`.
- **In-Memory Pipe**: The secret is generated via `kubectl create secret generic ... --dry-run=client -o yaml | kubectl apply -f -` without persisting YAML to disk.
- **Exclusion**: `.gitignore` explicitly ignores local secret and credential manifests (`*secret*.yaml`, `*credentials*.yaml`).

---

## 5. Security Posture

Grafana is configured under a strict zero-privilege container security policy:
- **Non-Root Execution**: Runs as non-root user `grafana` with UID `472`, GID `472`, and `fsGroup: 472`.
- **Privilege Escalation**: `allowPrivilegeEscalation: false`.
- **Linux Capabilities**: Dropped all capabilities (`capabilities: { drop: ["ALL"] }`).
- **Seccomp Profile**: `seccompProfile: { type: RuntimeDefault }`.
- **Read-Only Root Filesystem**: `readOnlyRootFilesystem: true`.
- **No Service Account Token**: `automountServiceAccountToken: false`.
- **Authentication Hardening**:
  - `GF_USERS_ALLOW_SIGN_UP=false`: Disables public sign-up.
  - `GF_AUTH_ANONYMOUS_ENABLED=false`: Anonymous access is strictly rejected (HTTP 401).
  - `GF_SECURITY_DISABLE_GRAVATAR=true`: Prevents external network requests.
  - `GF_ANALYTICS_REPORTING_ENABLED=false`, `GF_ANALYTICS_CHECK_FOR_UPDATES=false`, `GF_UPDATE_CHECK_ENABLED=false`: Disables external telemetry.
- **Prohibited Mounts**: No `hostPath`, no persistent volumes, no Docker socket, and no host kubeconfig mounts.

---

## 6. Storage Strategy and Ephemeral Limitation

- **Storage Mount**: `/var/lib/grafana` is mounted as an ephemeral `emptyDir` volume with a `256Mi` size limit.
- **Temp Mount**: `/tmp` is mounted as an `emptyDir` volume with a `64Mi` size limit.
- **Ephemeral Lifecycle**: Because `/var/lib/grafana` uses `emptyDir`, local user session state and temporary SQLite modifications are lost if the pod restarts. However, the data source and the KubePredict dashboard are fully provisioned from Kubernetes ConfigMaps and are re-created automatically upon pod restart. Persistent volume claims (PVCs) are not used in local development.

---

## 7. Data Source Provisioning

The internal Prometheus server is provisioned automatically via ConfigMap `kubepredict-grafana-datasource` mounted at `/etc/grafana/provisioning/datasources/`:

- **Name**: `KubePredict Prometheus`
- **UID**: `kubepredict-prometheus`
- **Type**: `prometheus`
- **Access Mode**: `proxy` (Grafana server queries Prometheus via internal cluster DNS)
- **URL**: `http://kubepredict-prometheus.kubepredict-monitoring.svc.cluster.local:9090`
- **Default**: `true`
- **Editable**: `false`

---

## 8. Dashboard Provider and Definition

Dashboard provisioning is defined via ConfigMap `kubepredict-grafana-dashboard-provider` mounted at `/etc/grafana/provisioning/dashboards/`:
- **Provider Name**: `KubePredict`
- **Folder**: `KubePredict`
- **Type**: `file`
- **Disable Deletion**: `true`
- **Editable**: `false`
- **Path**: `/var/lib/grafana/dashboards/kubepredict`

The dashboard definition is version-controlled at `monitoring/grafana/dashboards/kubepredict-overview.json` and mirrored in ConfigMap `kubepredict-grafana-dashboard`.
- **Title**: `KubePredict Overview`
- **UID**: `kubepredict-overview`
- **Refresh**: `15s`
- **Default Range**: `now-15m` to `now`
- **Tags**: `kubepredict`, `kubernetes`, `autoscaling`, `prometheus`

---

## 9. Dashboard Panels and Validated PromQL

The dashboard comprises 18 panels across 5 functional rows:

### Row 1: Service Health
1. **Workload Targets Up** (`stat`)
   - PromQL: `sum(up{job="kubepredict-workload"})`
   - Description: Total count of active KubePredict workload targets reported as up by Prometheus.
2. **Prometheus Workload Targets Up** (`stat`)
   - PromQL: `sum(up{job="kubepredict-workload"})`
   - Description: Scrape health only (not Kubernetes pod readiness).
3. **HTTP Request Rate** (`timeseries`)
   - PromQL: `sum(rate(apa_http_requests_total{job="kubepredict-workload"}[1m]))`
   - Description: Aggregate incoming HTTP request rate across all workload pods.
4. **Completed Work Request Rate** (`timeseries`)
   - PromQL: `sum(rate(apa_work_requests_total{job="kubepredict-workload",outcome="completed"}[1m]))`
   - Description: Aggregate rate of successfully completed work requests.

### Row 2: Latency and Errors
5. **p95 HTTP Request Duration** (`timeseries`)
   - PromQL: `histogram_quantile(0.95, sum by (le) (rate(apa_http_request_duration_seconds_bucket{job="kubepredict-workload"}[5m])))`
   - Unit: Seconds (`s`).
6. **Average HTTP Request Duration** (`timeseries`)
   - PromQL: `sum(rate(apa_http_request_duration_seconds_sum{job="kubepredict-workload"}[5m])) / (sum(rate(apa_http_request_duration_seconds_count{job="kubepredict-workload"}[5m])) > 0)`
   - Unit: Seconds (`s`).
7. **HTTP 5xx Error Rate** (`timeseries`)
   - PromQL: `sum(rate(apa_http_requests_total{job="kubepredict-workload",status_code=~"5.."}[1m]))`
   - Description: Server error rate per second.
8. **Work Outcome Totals** (`timeseries`)
   - PromQL: `sum by (outcome) (rate(apa_work_requests_total{job="kubepredict-workload"}[1m]))`
   - Description: Work request outcomes (`completed`, `controlled_failure`, `rejected`).

### Row 3: Application Work
9. **CPU Iterations Rate** (`timeseries`)
   - PromQL: `sum(rate(apa_cpu_iterations_total{job="kubepredict-workload"}[1m]))`
   - Description: Rate of simulated CPU iterations processed per second.
10. **Configured Delay Rate** (`timeseries`)
    - PromQL: `sum(rate(apa_artificial_delay_seconds_total{job="kubepredict-workload"}[1m]))`
    - Description: Rate of artificial delay seconds injected per second.
11. **p95 Work Duration** (`timeseries`)
    - PromQL: `histogram_quantile(0.95, sum by (le) (rate(apa_work_duration_seconds_bucket{job="kubepredict-workload"}[5m])))`
    - Unit: Seconds (`s`).
12. **Requests in Progress** (`stat`)
    - PromQL: `sum(apa_http_requests_in_progress{job="kubepredict-workload"})`
    - Description: Instantaneous count of concurrent in-flight requests.

### Row 4: Per-Pod View
13. **HTTP Request Rate by Pod** (`timeseries`)
    - PromQL: `sum by (pod) (rate(apa_http_requests_total{job="kubepredict-workload"}[1m]))`
14. **Work Request Rate by Pod** (`timeseries`)
    - PromQL: `sum by (pod) (rate(apa_work_requests_total{job="kubepredict-workload",outcome="completed"}[1m]))`
15. **p95 Request Duration by Pod** (`timeseries`)
    - PromQL: `histogram_quantile(0.95, sum by (pod, le) (rate(apa_http_request_duration_seconds_bucket{job="kubepredict-workload"}[5m])))`
16. **Target Scrape Status by Pod** (`timeseries`)
    - PromQL: `up{job="kubepredict-workload"}`

### Row 5: Kubernetes Replica Context (Static Information Panels)
17. **Kubernetes Replica State Baseline** (`text`)
    - Static markdown documenting fixed baseline manifest (2 replicas), absence of live HPA, and that `kube-state-metrics` is not installed.
18. **Metrics Server & Prometheus Scope** (`text`)
    - Static markdown clarifying that Metrics Server serves resource metrics to HPA and is not a historical Prometheus scrape source.

---

## 10. Operational Procedures

### Deploying Grafana
```powershell
powershell -ExecutionPolicy Bypass -File scripts\kubernetes\deploy-grafana.ps1
```

### Inspecting Grafana
```powershell
powershell -ExecutionPolicy Bypass -File scripts\kubernetes\inspect-grafana.ps1
```
Or with a local port check:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\kubernetes\inspect-grafana.ps1 -LocalPort 3000
```

### Accessing Grafana Locally
```powershell
powershell -ExecutionPolicy Bypass -File scripts\kubernetes\port-forward-grafana.ps1 -LocalPort 3000
```
Navigate to `http://127.0.0.1:3000` and sign in with `admin` and the credentials stored in Secret `grafana-admin-credentials`.

To view the generated password securely from the cluster:
```powershell
[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String((kubectl get secret grafana-admin-credentials -n kubepredict-monitoring --context kind-kubepredict-dev -o jsonpath="{.data.admin-password}")))
```

### Removing Grafana
```powershell
powershell -ExecutionPolicy Bypass -File scripts\kubernetes\remove-grafana.ps1 -ConfirmGrafanaRemoval
```
*(Note: Do not execute during Phase 6B)*.

---

## 11. Scope Boundaries and Limitations

- **No Comparative Experimentation**: No benchmark, load test, or research comparison has been performed.
- **No Forecasting or Autoscaler Controller**: Machine learning models, predictive controllers, and custom metric adapters are intentionally absent in Phase 6B.
- **Absence of kube-state-metrics**: Prometheus currently only scrapes application metrics and its own internal metrics. Pod counts and deployment replica states are verified via `kubectl`.
