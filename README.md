# KubePredict: Adaptive Predictive Autoscaling for Cloud-Native Applications Using Kubernetes

A research-oriented university project comparing:

1. Fixed Kubernetes replicas
2. Default Kubernetes Horizontal Pod Autoscaler (HPA)
3. Predictive autoscaling
4. Guarded predictive autoscaling with confidence checks, burst override, drift handling, cooldown, bounded scaling, and reactive fallback

## Current Repository State

Implementation is performed through externally supplied, phase-gated AI builder instructions.

Implemented:
- Python project foundation & typed data contracts
- FastAPI workload service with Prometheus metrics
- Multi-stage non-root Linux container (`kubepredict-workload-api:0.1.0`)
- Local Kubernetes cluster environment (`kind` / `kubepredict-dev`)
- Local image loading into kind nodes
- Fixed-replica declarative manifests (`kubernetes/base/`)
- Live Kubernetes deployment to `kubepredict-dev` in `kubepredict-system` namespace
- Runtime verification: 2 Ready replicas, ClusterIP Service, probes, security isolation, endpoint smoke tests
- Kubernetes Metrics Server (`v0.7.2`) deployed and validated with `patch-kind.yaml` (Phase 5A)
- Resource metrics API (`v1beta1.metrics.k8s.io`) and `kubectl top nodes` / `kubectl top pods` verification
- Horizontal Pod Autoscaler (HPA) baseline manifests (`autoscaling/v2`) and static validation (Phase 5B)
- Horizontal Pod Autoscaler live activation and functional scale-up / scale-down validation; HPA removed and fixed 2-replica baseline restored (Phase 5C)
- Lightweight Prometheus server (`prom/prometheus:v2.54.1`) deployed in `kubepredict-monitoring`, Kubernetes pod discovery, and PromQL verified (Phase 6A)
- Official Grafana OSS (`grafana/grafana:11.2.0`) deployed in `kubepredict-monitoring`, automated Prometheus datasource provisioning, dashboard provider, and KubePredict Overview dashboard with 18 panels (Phase 6B)
- Deterministic request scenario configuration framework based on Locust (Phase 7A)
- Five configuration-driven scenario profiles (`steady`, `ramp`, `periodic`, `sudden_increase`, `drift`)
- Headless, bounded CLI runner with stage scheduling and run metadata logging
- Offline dry-run scenario validation (`validate-scenarios.ps1`)
- Bounded cluster smoke test execution (`smoke-test.ps1`)
- Kubernetes request scenario validation and aligned telemetry dataset capture (Phase 7B)
- Telemetry capture pipeline querying Prometheus (15s aligned step) and Kubernetes cluster resource metrics
- Aligned raw datasets captured for all 5 scenarios under `data/raw/scenarios/` (strictly git-ignored)
- Automated dataset validation (structure, monotonicity, non-negative rates, fixed-replica invariants)
- Offline time-series forecasting pipeline & model-independent contracts (Phase 8A)
- Four forecasting baseline families (Persistence, Trailing Moving Average, EWMA, Linear Trend)
- Strict chronological rolling-origin evaluation without future data leakage
- Comprehensive metrics (MAE, RMSE, sMAPE, Bias) and provisional candidate selection
- Comprehensive unit, integration, and manifest test suites

Not implemented:
- Live Prometheus metric polling for controller operation (scheduled for future phase)
- Replica calculation and scaling recommendation logic (scheduled for future phase)
- Predictive scaling controller and forecast guard (scheduled for future phase)
- Live cluster autoscaling and controller comparisons (scheduled for future phase)
- Formal comparative experiment execution and benchmark claims (scheduled for future phase)
- Statistical result analysis and performance conclusions (scheduled for future phase)

## Open-source stack

Python, FastAPI, Docker, Kubernetes (`kind`), Prometheus, Grafana, Locust, pandas, NumPy, scikit-learn or statsmodels, pytest, Git.

## Research constraint

Never claim improved latency, resource efficiency, prediction accuracy, or SLO compliance until experiments produce saved evidence. Planned behavior and measured results must remain clearly separated.

## Expected final comparison

- `fixed`: static replicas
- `hpa`: standard reactive baseline
- `predictive`: forecast-driven baseline
- `guarded`: prediction plus live correction and safety controls

## Project Structure
- `app/`: FastAPI workload service and Prometheus metrics.
- `shared/`: Enums, Pydantic models, validation logic.
- `config/`: Type-safe settings loading.
- `kubernetes/base/`: Declarative Kustomize base manifests (Namespace, ConfigMap, Deployment, Service).
- `kubernetes/kind/`: kind cluster topology configuration.
- `scripts/docker/`: Docker build, run, test, and stop helper scripts.
- `scripts/kubernetes/`: Kubernetes cluster management, manifest deployment, inspection, and port-forwarding scripts.
- `tests/`: Pytest test suite (unit and integration tests).

## Verification & Testing
Run automated checks:
```powershell
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m mypy app controller forecasting config shared tests
.venv\Scripts\python.exe -m pytest
```

## Running Locally (Standalone)
Start the server locally:
```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Probe the endpoints using PowerShell:
```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/
Invoke-RestMethod -Uri http://127.0.0.1:8000/work -Method Post -Body '{"cpu_iterations":100}' -ContentType "application/json"
```

## Docker Packaging & Local Container
The application is packaged as a multi-stage, non-root Linux container.
- Image Names: `kubepredict-workload-api:0.1.0` (preferred), `adaptive-predictive-k8s-autoscaler:0.1.0`
- Base Image: `python:3.14-slim`

PowerShell helper workflow:
```powershell
.\scripts\docker\build.ps1
.\scripts\docker\run.ps1
.\scripts\docker\smoke-test.ps1
.\scripts\docker\stop.ps1
```

## Phase 4: Local Kubernetes Environment & Fixed-Replica Baseline (Phase 4A/4B/4C)
The project uses `kind` (Kubernetes IN Docker) for local cluster orchestration with a live fixed-replica baseline deployed.

- **Cluster Name:** `kubepredict-dev`
- **Context:** `kind-kubepredict-dev`
- **Topology:** Single control-plane node (`kubepredict-dev-control-plane`, Ready)
- **Loaded Image:** `kubepredict-workload-api:0.1.0` (present in node containerd runtime)
- **Namespace:** `kubepredict-system`
- **Deployment:** `kubepredict-workload-api`
- **Service:** `kubepredict-workload-api` (ClusterIP on port 8000 -> targetPort `http`)
- **Fixed Replicas:** 2 desired, 2 updated, 2 available, 2 Ready (`Running`, 0 restarts)
- **Manifest Location:** `kubernetes/base/`
  - `namespace.yaml` (`kubepredict-system`)
  - `configmap.yaml` (`kubepredict-workload-api-config`)
  - `deployment.yaml` (`kubepredict-workload-api`, 2 replicas, security hardened)
  - `service.yaml` (`kubepredict-workload-api`, ClusterIP)
  - `kustomization.yaml`

> [!IMPORTANT]
> The fixed-replica baseline remains deployed and the `kubepredict-dev` cluster remains active to provide the baseline environment for Phase 5B (Monitoring Stack).
> Note: Horizontal Pod Autoscaler (HPA), Prometheus server, Grafana, forecasting models, and custom predictive autoscaling are **not yet implemented**. No comparative performance experiments have been conducted.

### Deployment Inspection & Verification Commands
```powershell
# Inspect all baseline resources
.\scripts\kubernetes\inspect-fixed.ps1

# Or via kubectl directly
kubectl get all -n kubepredict-system --context kind-kubepredict-dev -o wide
```

### Temporary Port-Forwarding & Smoke Testing
```powershell
# Start temporary port forward to service on local port 8000
.\scripts\kubernetes\port-forward.ps1 -LocalPort 8000

# Smoke-test endpoints from another shell
Invoke-RestMethod -Uri http://127.0.0.1:8000/healthz
Invoke-RestMethod -Uri http://127.0.0.1:8000/readyz
Invoke-RestMethod -Uri http://127.0.0.1:8000/work -Method Post -ContentType "application/json" -Body '{"cpu_iterations": 1000, "delay_ms": 10, "payload_size": 10}'
```

### Baseline Management & Removal Safeguards
```powershell
# Re-apply or update fixed baseline
.\scripts\kubernetes\deploy-fixed.ps1

# Remove fixed baseline (guarded by required confirmation switch)
.\scripts\kubernetes\remove-fixed.ps1 -ConfirmFixedBaselineRemoval

# Cluster teardown (guarded by required confirmation switch)
.\scripts\kubernetes\delete-cluster.ps1 -ConfirmProjectCluster
```

## Phase 5A: Kubernetes Metrics Server (Implemented & Validated)
Kubernetes Metrics Server (`v0.7.2`) is deployed to `kube-system` to supply pod and node resource metrics for the future HPA baseline.

- **Version:** `v0.7.2` (official upstream: [kubernetes-sigs/metrics-server](https://github.com/kubernetes-sigs/metrics-server))
- **Manifest Location:** `kubernetes/metrics-server/` (`upstream.yaml`, `patch-kind.yaml`, `kustomization.yaml`)
- **Compatibility Patch:** Strategic merge patch adding `--kubelet-insecure-tls` for local `kind` Kubelet certificate scraping.
- **Resource Metrics API:** `v1beta1.metrics.k8s.io` reports `Available=True`.

### Metrics Server Operations & Verification Commands
```powershell
# Install Metrics Server
.\scripts\kubernetes\install-metrics-server.ps1

# Inspect Metrics Server and check resource metrics
.\scripts\kubernetes\inspect-metrics-server.ps1

# Node resource metrics
kubectl top nodes --context kind-kubepredict-dev

# Workload pod resource metrics
kubectl top pods -n kubepredict-system --context kind-kubepredict-dev --containers

# Removal safeguard (requires confirmation switch)
.\scripts\kubernetes\remove-metrics-server.ps1 -ConfirmMetricsServerRemoval
```

> [!NOTE]
> Horizontal Pod Autoscaler (HPA) was **not deployed** in Phase 5A. The fixed-replica baseline remains live in `kubepredict-system`.

## Phase 5B: Kubernetes HPA Baseline (Statically Validated - Not Live)

A declarative Horizontal Pod Autoscaler (HPA) baseline configuration is established in `kubernetes/hpa/` using `autoscaling/v2`.

- **API Version:** `autoscaling/v2`
- **Target Deployment:** `kubepredict-workload-api` (namespace: `kubepredict-system`)
- **Replica Bounds:** `minReplicas: 1`, `maxReplicas: 6`
- **Target Metric:** Resource `cpu` with `averageUtilization: 60` (calculated relative to container CPU request of `100m`)
- **Scaling Behavior Policies:**
  - **Scale-Up:** `stabilizationWindowSeconds: 0`, `selectPolicy: Max` (+2 pods or +100% per 60s)
  - **Scale-Down:** `stabilizationWindowSeconds: 300`, `selectPolicy: Max` (-1 pod or -50% per 60s)
- **Manifest Location:** `kubernetes/hpa/` (`hpa.yaml`, `kustomization.yaml`)

> [!IMPORTANT]
> The HPA is **not yet applied to the live cluster**. The live workload continues to run as a fixed-replica baseline with exactly 2 Ready pods.
> No live HPA object currently exists, and no autoscaling behaviour has been observed. Live activation and validation are scheduled for **Phase 5C**.

### HPA Render & Dry-Run Commands
```powershell
# Render HPA resource locally
kubectl kustomize kubernetes/hpa

# Validate HPA against the cluster via server-side dry-run (does not mutate cluster)
kubectl apply --dry-run=server -k kubernetes/hpa --context kind-kubepredict-dev

# Verify that no live HPA object exists
kubectl get hpa -n kubepredict-system --context kind-kubepredict-dev
```

### HPA Operations & Lifecycle Scripts
```powershell
# Deploy HPA baseline to live cluster
.\scripts\kubernetes\deploy-hpa.ps1

# Inspect HPA status and pod metrics (read-only)
.\scripts\kubernetes\inspect-hpa.ps1

# Safe HPA removal (requires explicit confirmation switch; does not modify deployment replicas)
.\scripts\kubernetes\remove-hpa.ps1 -ConfirmHpaRemoval
```

## Phase 5C: HPA Baseline Functional Validation (Completed)

The reactive HPA baseline was applied to `kubepredict-dev`, and controlled load was injected to validate scale-up and scale-down dynamics:
- **Scale-Up Observed**: CPU spiked to 251% of request, triggering HPA scale-up from 2 -> 4 -> 6 replicas (all pods reached Ready, 0 restarts).
- **Scale-Down Observed**: Following demand cessation, after the 300s stabilization window, replicas scaled down from 6 -> 4 -> 3 -> 2 pods.
- **HPA Cleanup**: The HPA was safely removed (`remove-hpa.ps1`), and the declarative fixed 2-replica baseline (`kubernetes/base/`) was restored and verified.

## Phase 6A: Prometheus Server Deployment & Scrape Validation (Implemented & Validated)

A standalone, lightweight Prometheus server (`prom/prometheus:v2.54.1`) is deployed in namespace `kubepredict-monitoring` with in-cluster Kubernetes pod service discovery.

- **Namespace**: `kubepredict-monitoring`
- **Image**: `prom/prometheus:v2.54.1` (official Prometheus LTS image)
- **Deployment & Service**: `kubepredict-prometheus` (1 replica, ClusterIP on port 9090)
- **Storage**: `emptyDir` (sizeLimit: 1Gi) with 6-hour retention (`--storage.tsdb.retention.time=6h`, `--storage.tsdb.retention.size=750MB`)
- **Scrape Jobs**:
  - `prometheus`: Scrapes localhost:9090 self-monitoring metrics.
  - `kubepredict-workload`: Dynamic Kubernetes pod discovery in `kubepredict-system` targeting running pods with labels `app.kubernetes.io/name=kubepredict` and `app.kubernetes.io/component=workload-api`.
- **Target Verification**: Exactly 2 active workload targets (`10.244.0.5:8000`, `10.244.0.6:8000`) reporting health `up` with 0 scrape errors.
- **PromQL Verification**: Validated queries (`up`, `apa_application_info`, `apa_http_requests_total`, `apa_work_requests_total`, etc.).

### Prometheus Operations & Verification Commands
```powershell
# Deploy Prometheus
.\scripts\kubernetes\deploy-prometheus.ps1

# Inspect Prometheus deployment and status
.\scripts\kubernetes\inspect-prometheus.ps1

# Temporary port-forward to localhost:9090
.\scripts\kubernetes\port-forward-prometheus.ps1 -LocalPort 9090

# Validate scrape targets and PromQL queries
.\scripts\kubernetes\validate-prometheus-scrape.ps1

# Remove Prometheus stack (requires explicit confirmation switch)
.\scripts\kubernetes\remove-prometheus.ps1 -ConfirmPrometheusRemoval
```

## Phase 6B: Grafana Visualization & Dashboard Provisioning (Implemented & Validated)

Official Grafana OSS (`grafana/grafana:11.2.0`) is deployed in `kubepredict-monitoring` with automated Prometheus data source provisioning and a version-controlled dashboard.

- **Namespace**: `kubepredict-monitoring`
- **Image**: `grafana/grafana:11.2.0`
- **Deployment & Service**: `kubepredict-grafana` (1 replica, ClusterIP on port 3000)
- **Security Profile**: Non-root UID 472, read-only rootfs, dropped `ALL` capabilities, `RuntimeDefault` seccomp
- **Automated Provisioning**:
  - Datasource: `kubepredict-prometheus` automatically configured via ConfigMap
  - Dashboard Provider: Automatically loads `kubepredict-overview.json` ConfigMap
  - Dashboard: 18 panels across 5 functional rows covering cluster replicas, request rates, error rates, CPU/Memory usage, and synthetic workload mechanics
- **Credentials**: Admin credentials provisioned via Kubernetes Secret (`grafana-admin-credentials`)

### Grafana Operations & Verification Commands
```powershell
# Deploy Grafana stack
.\scripts\kubernetes\deploy-grafana.ps1

# Inspect Grafana status
.\scripts\kubernetes\inspect-grafana.ps1

# Temporary port-forward to localhost:3000
.\scripts\kubernetes\port-forward-grafana.ps1 -LocalPort 3000

# Validate health, datasource connectivity, and dashboard provisioning
.\scripts\kubernetes\validate-grafana.ps1

# Remove Grafana stack (requires explicit confirmation switch)
.\scripts\kubernetes\remove-grafana.ps1 -ConfirmGrafanaRemoval
```

## Phase 7A: Reproducible Request Scenario Framework (Implemented & Validated)

A deterministic, configuration-driven request scenario framework built with Locust for generating reproducible traffic profiles.

- **Package**: `load_testing/`
- **Configuration Contract**: Strict Pydantic models ([`load_testing/config.py`](file:///load_testing/config.py)) enforcing bounded duration (<=900s), user count (<=50), spawn rates (<=10/s), CPU iterations (<=400k), and localhost-only default destination.
- **Scenario Profiles**:
  - `steady` (180s, 12 users, constant moderate load)
  - `ramp` (240s, 16 users, step-wise increase)
  - `periodic` (240s, 14 users, repeated high/low cycles)
  - `sudden_increase` (180s, 18 users, abrupt step-change)
  - `drift` (240s, 18 users, changed duration and amplitude)
- **Locust Behavior**: Headless, deterministic load shape mapping elapsed time to stages; simulated `KubePredictUser` issuing `POST /work` with configured CPU load, sleep delay, response payload, and request timeout.
- **Run Metadata**: Standardized JSON record capturing git commit/dirty status, configuration SHA-256 hash, stage schedule, and Locust request totals.
- **Output Handling**: Runs saved to `experiments/output/<run-id>/` (strictly ignored by Git).

> [!IMPORTANT]
> The scenario framework is validated via offline dry-runs and a minimal 15-second smoke test. **No formal benchmarks, comparative experiments, or autoscaling evaluations have been executed in this phase.**

### Request Scenario Validation & Smoke Commands
```powershell
# Validate all scenario profiles offline (dry-run, no networking)
.\scripts\load-testing\validate-scenarios.ps1

# Run individual scenario in dry-run mode
.venv\Scripts\python.exe -m load_testing.runner --scenario steady --output-root experiments/output --dry-run

# Execute bounded 15s smoke test against live Kubernetes workload
.\scripts\load-testing\smoke-test.ps1 -LocalPort 18000
```

## Phase 7B: Kubernetes Scenario Validation & Telemetry Dataset Capture (Implemented & Validated)

The five request scenarios were executed sequentially against the live `kubepredict-workload-api` Service in the `kubepredict-dev` cluster, capturing aligned Prometheus telemetry and Kubernetes resource metrics for future offline forecasting.

- **Workload Target**: Fixed 2-replica baseline (`kubepredict-workload-api` in `kubepredict-system`).
- **Cluster Precondition**: Verified 2/2 Ready pods (0 restarts), Metrics Server active, Prometheus 3/3 targets healthy, no live HPA or predictive controller.
- **Scenarios Validated**:
  - `steady` (213.4s, 9,499 requests, 0 failures, 15 samples)
  - `ramp` (272.1s, 14,850 requests, 0 failures, 19 samples)
  - `periodic` (276.2s, 10,726 requests, 0 failures, 19 samples)
  - `sudden_increase` (212.9s, 10,273 requests, 0 failures, 15 samples)
  - `drift` (276.6s, 13,211 requests, 0 failures, 19 samples)
- **Aggregated Traffic**: 58,559 requests across all 5 scenarios with **0 failures** and **0 dropped connections**.
- **Telemetry Specifications**:
  - Query Specification: `experiments/configs/telemetry_queries.yaml` (10 system-level queries + 2 pod-level queries).
  - Sample Interval: Aligned 15.00s step grid matching Prometheus scrape interval.
  - Collector Layer: Read-only `kubectl` subprocess queries for deployment replica counts, pod readiness/restarts, and `kubectl top` resource usage.
  - Automated Validation: Strict Pydantic and tabular validation checking CSV/JSON file presence, schema column completeness, strictly monotonic UTC timestamps, non-negative rates, config hash stability, and fixed-replica invariant (`desired=2`).
- **Dataset Storage**: Saved under `data/raw/scenarios/<scenario-name>/` with index at `data/raw/scenarios/dataset-index.json` (strictly ignored by Git).
- **Documentation**: Comprehensive schema and dataset documentation in [`docs/TELEMETRY_DATASET.md`](docs/TELEMETRY_DATASET.md).

> [!NOTE]
> The captured datasets represent raw telemetry under the fixed 2-replica baseline. In accordance with project research constraints, **no forecasting models, predictive controllers, or comparative performance conclusions are drawn in this phase**.

### Telemetry Capture & Dataset Inspection Commands
```powershell
# Validate telemetry plan and query definitions offline
.\scripts\experiments\validate-telemetry-plan.ps1

# Capture a single scenario against live cluster
.\scripts\experiments\capture-scenario.ps1 -Scenario steady

# Execute all 5 scenarios sequentially with validation
.\scripts\experiments\capture-all-scenarios.ps1

# Inspect and validate captured dataset
.\scripts\experiments\inspect-dataset.ps1 -Scenario steady
```

## Phase 8A: Offline Forecasting Baselines & Chronological Evaluation (Implemented & Validated)

A model-independent offline forecasting pipeline designed to evaluate time-series forecasting models against the Phase 7B telemetry datasets under strict chronological validation.

- **Target Variable**: `request_rate_per_second` (HTTP requests per second across 1, 2, and 4-step horizons: 15s, 30s, and 60s).
- **Data Source**: Phase 7B scenario datasets in `data/raw/scenarios/` (`steady`, `ramp`, `periodic`, `sudden_increase`, `drift`).
- **Forecasting Models**:
  - **Persistence Forecaster**: Mandatory naive baseline repeating the latest observation ($y_t$).
  - **Trailing Moving Average**: Mean of trailing window $w \in \{3, 5, 8\}$ samples.
  - **EWMA Forecaster**: Recursive exponential smoothing with $\alpha \in \{0.2, 0.4, 0.6, 0.8\}$.
  - **Linear Trend Forecaster**: Trailing OLS regression over time index with window $w \in \{3, 5, 8\}$ samples.
- **Chronological Split Policy**: 60% training, 20% validation, 20% test per scenario run. No shuffling, zero future data leakage.
- **Rolling-Origin Evaluation**: Step-by-step rolling origins advancing by 1 step. Target timestamps strictly bounded within partition. Excluded origins (due to missing values or history bounds) recorded in `excluded_origins.csv`.
- **Metrics Evaluated**: MAE (primary), RMSE, sMAPE (0-200% symmetric range), and Bias. Aggregated at macro and weighted levels across scenarios.
- **Baseline Comparison Rule & Provisional Selection**:
  - Horizon 1 (15s): **Persistence** retained as provisional candidate (Validation Macro MAE = 5.0882).
  - Horizon 2 (30s): **Persistence** retained as provisional candidate (Validation Macro MAE = 11.3645).
  - Horizon 4 (60s): **Linear Trend** (`window=8`) selected as provisional candidate (Validation Macro MAE = 18.7430 vs Persistence 27.4432, diff = -8.7002).
  - Overall Provisional Candidate: `linear_trend` (`window=8`).
- **Output Storage**: Detailed CSV and JSON evidence stored under `data/processed/forecasting/<eval-id>/` and `results/summaries/forecasting/<eval-id>/` (strictly git-ignored).
- **Documentation**: Full mathematical specification and evaluation protocol in [`docs/FORECASTING.md`](docs/FORECASTING.md).

> [!IMPORTANT]
> All candidate selections are strictly offline and provisional for Phase 8B review. In accordance with project research constraints, **no controller code, replica recommendations, live scaling, or autoscaling performance conclusions are drawn**.

### Offline Forecasting Operations & Verification Commands
```powershell
# Validate forecasting configuration and Phase 7B datasets offline (0 predictions, 0 mutations)
.\scripts\forecasting\validate-data.ps1

# Run complete chronological offline evaluation across all Phase 7B scenarios
.\scripts\forecasting\evaluate-baselines.ps1

# Inspect evaluation output directory, candidate selection, and validation rankings
.\scripts\forecasting\inspect-results.ps1 -EvaluationDir data/processed/forecasting/<eval-id>
```





