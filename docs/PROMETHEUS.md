# KubePredict Prometheus Monitoring Architecture & Specification

## 1. Overview and Purpose

Prometheus provides the time-series observability foundation for KubePredict:

> "KubePredict: Adaptive Predictive Autoscaling for Cloud-Native Applications Using Kubernetes"

In Phase 6A, a standalone, lightweight Prometheus server is deployed into the project-owned Kubernetes cluster (`kubepredict-dev`) to continuously scrape and persist metrics from the KubePredict FastAPI workload service and Prometheus self-monitoring.

---

## 2. Distinction Between Metrics Server and Prometheus

Kubernetes Metrics Server and Prometheus serve distinct, complementary roles in this architecture:

| Attribute | Metrics Server (Phase 5A) | Prometheus (Phase 6A) |
|---|---|---|
| **Namespace** | `kube-system` | `kubepredict-monitoring` |
| **Data Scope** | Ephemeral, current CPU and memory utilization | Multi-dimensional time-series metrics with labels |
| **API Surface** | `metrics.k8s.io/v1beta1` | Prometheus HTTP Query & Scrape API (`/api/v1/`) |
| **Primary Consumer** | Kubernetes Horizontal Pod Autoscaler (HPA) | Observability dashboards, predictive autoscaling controller |
| **Persistence** | None (in-memory sliding window, ~15s) | TSDB with bounded 6-hour local retention |
| **Query Engine** | Read-only resource summaries (`kubectl top`) | PromQL (Prometheus Query Language) |

> [!IMPORTANT]
> Prometheus does **not** replace Metrics Server. HPA relies directly on Metrics Server for reactive autoscaling, whereas the future predictive controller will query Prometheus for application request rates, latency distributions, and fine-grained CPU utilization traces.

---

## 3. Version and Image Selection

- **Image Repository**: `prom/prometheus` (Official Prometheus Docker Hub repository)
- **Version Tag**: `v2.54.1`
- **Full Reference**: `prom/prometheus:v2.54.1`
- **Digest**: `sha256:f6639335d34a77d9d9db382b92eeb7fc00934be8eae81dbc03b31cfe90411a94`
- **Selection Rationale**: `v2.54.1` is an official, stable release of the Prometheus 2.x LTS line with native support for TSDB size-bounded retention (`--storage.tsdb.retention.size`), robust Kubernetes service discovery (`kubernetes_sd_configs`), low baseline memory overhead, and proven stability across linux/amd64 container runtimes.

Floating tags (`latest`, `main`, `master`) and unversioned major tags (`v2`) are strictly prohibited to maintain build determinism.

---

## 4. Workload Discovery and Scrape Configuration

Prometheus discovers workload pods dynamically using the Kubernetes API via in-cluster service account authentication.

### Scrape Configuration

Prometheus is configured via ConfigMap `kubepredict-prometheus-config` in namespace `kubepredict-monitoring`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  # 1. Prometheus Server Self-Monitoring
  - job_name: prometheus
    static_configs:
      - targets: ['localhost:9090']

  # 2. KubePredict Workload Pods Discovery
  - job_name: kubepredict-workload
    kubernetes_sd_configs:
      - role: pod
        namespaces:
          names:
            - kubepredict-system
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_phase]
        action: keep
        regex: Running
      - source_labels: [__meta_kubernetes_pod_label_app_kubernetes_io_name]
        action: keep
        regex: kubepredict
      - source_labels: [__meta_kubernetes_pod_label_app_kubernetes_io_component]
        action: keep
        regex: workload-api
      - source_labels: [__meta_kubernetes_pod_container_port_name]
        action: keep
        regex: http
      - action: replace
        target_label: __metrics_path__
        replacement: /metrics
      - source_labels: [__meta_kubernetes_namespace]
        action: replace
        target_label: namespace
      - source_labels: [__meta_kubernetes_pod_name]
        action: replace
        target_label: pod
      - source_labels: [__meta_kubernetes_pod_container_name]
        action: replace
        target_label: container
```

### Cardinality and Scrape Guardrails

- **Namespace Scope**: Discovery is strictly constrained to `kubepredict-system`.
- **Target Filtering**: Only pods with phase `Running`, labels `app.kubernetes.io/name=kubepredict` and `app.kubernetes.io/component=workload-api`, and port name `http` are scraped.
- **Label Hygiene**: Internal `__meta_*` labels are filtered out; only bounded, low-cardinality target labels (`namespace`, `pod`, `container`, `job`) are retained.
- **Metrics Endpoint**: Workload pods expose Prometheus metrics on port 8000 under `/metrics`.

---

## 5. Storage, Retention, and Resource Sizing

- **Storage Volume**: `emptyDir` mounted at `/prometheus` with `sizeLimit: 1Gi`.
- **Time Retention**: `--storage.tsdb.retention.time=6h`
- **Size Retention**: `--storage.tsdb.retention.size=750MB`
- **Resource Requests**: CPU: `100m`, Memory: `256Mi`
- **Resource Limits**: CPU: `500m`, Memory: `512Mi`

> [!NOTE]
> Local development uses ephemeral `emptyDir` storage. Prometheus data is reset if the Prometheus pod is deleted or rescheduled. Long-term experiment artifacts will be captured and exported separately in future phases.

---

## 6. RBAC and Security Profile

Prometheus runs with the principle of least privilege:

### RBAC (`kubepredict-prometheus-discovery`)
- **ServiceAccount**: `kubepredict-prometheus` (in `kubepredict-monitoring`)
- **Automount Token**: `true` (strictly required for in-cluster API server service discovery)
- **ClusterRole**:
  - Resources: `nodes`, `services`, `endpoints`, `pods` (API group: `""`)
  - Verbs: `get`, `list`, `watch`
  - Secrets: **Forbidden** (no access)
  - Mutation: **Forbidden** (no write/update/delete verbs)
  - ClusterAdmin: **Forbidden**

### Container Security
- **User / Group**: UID `65534` (`nobody`), GID `65534` (`nobody`), `fsGroup: 65534`
- **Non-Root**: `runAsNonRoot: true`
- **Privilege Escalation**: `allowPrivilegeEscalation: false`
- **Capabilities**: `drop: [ALL]`
- **Root Filesystem**: `readOnlyRootFilesystem: true`
- **Seccomp Profile**: `RuntimeDefault`
- **External Exposure**: Service is `ClusterIP` on port 9090 (no NodePort, LoadBalancer, or Ingress).

---

## 7. Operational Scripts

The monitoring stack is operated and inspected via PowerShell scripts:

1. **Deploy Prometheus**:
   ```powershell
   .\scripts\kubernetes\deploy-prometheus.ps1
   ```
   Validates prerequisites, renders Kustomize manifests, executes dry run, applies resources, and waits for deployment rollout.

2. **Inspect Prometheus**:
   ```powershell
   .\scripts\kubernetes\inspect-prometheus.ps1
   ```
   Read-only inspection of deployment status, pod specifications, resource allocations, endpoints, RBAC rules, events, and recent logs.

3. **Temporary Port-Forward**:
   ```powershell
   .\scripts\kubernetes\port-forward-prometheus.ps1 -LocalPort 9090
   ```
   Binds `localhost:9090` to the Prometheus Service for temporary administrative access.

4. **Validate Scrape and PromQL**:
   ```powershell
   .\scripts\kubernetes\validate-prometheus-scrape.ps1
   ```
   Validates scrape targets, executes baseline PromQL queries, tests metric counter increments with synthetic workload requests, and verifies clean process termination.

5. **Guarded Removal**:
   ```powershell
   .\scripts\kubernetes\remove-prometheus.ps1 -ConfirmPrometheusRemoval
   ```
   Safely deletes only Prometheus monitoring manifests from `kubernetes/monitoring/prometheus`.

---

## 8. Verified PromQL Queries

The following PromQL queries were validated against the live Prometheus server:

- `up`: Returns `1` for all active scrape targets (Prometheus server and both workload replicas).
- `up{job="kubepredict-workload"}`: Confirms both workload pods (`kubepredict-workload-api-*`) are healthy scrape targets.
- `apa_application_info`: Exposes application version, environment, and build metadata.
- `apa_http_requests_total`: Tracks incoming HTTP requests partitioned by method, route, and status code.
- `apa_http_request_duration_seconds_count`: Tracks latency histogram observation counts across routes.
- `apa_http_requests_in_progress`: Real-time gauge of active concurrent requests.
- `apa_work_requests_total`: Tracks completed, rejected, and failed synthetic workload jobs.
- `apa_cpu_iterations_total`: Cumulative counter of CPU iteration cycles executed by workload pods.
- `apa_artificial_delay_seconds_total`: Cumulative artificial sleep time injected by workload jobs.

---

## 9. Current Limitations & Scope Boundaries

- **Grafana**: Grafana dashboards and visualization servers are **not implemented** in Phase 6A.
- **Alertmanager**: Alerting and recording rules are disabled.
- **Remote Storage**: No remote write, remote read, or cloud persistent storage is configured.
- **Predictive Controller**: Custom controller, ML models, and predictive autoscaling remain planned for subsequent phases.
- **Comparative Benchmarking**: No comparative performance evaluation has been conducted.
