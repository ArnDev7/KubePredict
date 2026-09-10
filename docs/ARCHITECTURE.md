# Target Architecture

```text
Load generator -> FastAPI service -> Prometheus metrics
                         ^                 |
                         |                 v
                   Kubernetes        Forecasting
                         ^                 |
                         |                 v
                    Controller <- Guarded scaling policy
```

Controller modes:
- observe-only
- dry-run
- live scaling

Safety order:
1. Validate telemetry freshness.
2. Detect burst conditions.
3. Produce and validate forecast.
4. Estimate required replicas.
5. Apply confidence/drift rules.
6. Apply min/max bounds and bounded step.
7. Apply cooldown and stabilization.
8. Log decision and reason.
9. Scale only when live mode is explicitly enabled.

### Phase 1: Foundation (Implemented)
- Core types and Enums
- Project structure
- Testing harness

### Phase 2: Target Application (Implemented)
- FastAPI test workload service
- Configurable synthetic CPU load and delay capabilities
- Prometheus `/metrics` exposition for metrics aggregation

### Phase 3: Docker Packaging (Implemented)
- Multi-stage non-root container image (`kubepredict-workload-api:0.1.0`)
- Hardened secure-runtime profile (read-only rootfs, `no-new-privileges:true`)

### Phase 4A: Local Cluster Environment (Implemented)
- `kind` local cluster `kubepredict-dev` provisioned
- Image side-loaded into cluster nodes

### Phase 4B: Fixed-Replica Manifests (Implemented)
- Declarative Kustomize base (`kubernetes/base/`)
- Namespace (`kubepredict-system`), ConfigMap, Deployment (2 replicas), ClusterIP Service
- Manifest validation tests

### Phase 4C: Application Deployment & Verification (Implemented & Runtime-Verified)
- Live workload deployment to `kubepredict-dev` in namespace `kubepredict-system`
- Fixed-replica Deployment with exactly 2 desired, updated, available, and Ready replicas
- ClusterIP Service routing to two verified ready backend endpoints
- Verified health (`/healthz`) and readiness (`/readyz`) probes
- Resource requests (100m CPU, 128Mi Mem) and limits (500m CPU, 256Mi Mem)
- Workload security controls (non-root UID 1000, read-only rootfs, drop capabilities ALL, /tmp emptyDir)
- Functional endpoint smoke tests verified (root, health, ready, work, request-ID round trip, 422 limit rejection, metrics)
- Independent pod functionality verified across both replicas; port-forwards cleanly closed

### Phase 5A: Kubernetes Metrics Server (Implemented & Runtime-Verified)
- Metrics Server v0.7.2 deployed to `kube-system` via Kustomize overlay (`patch-kind.yaml`)
- Aggregated resource metrics API (`v1beta1.metrics.k8s.io`) active and Available
- Node resource metrics verified (`kubectl top nodes`)
- Workload pod and container resource metrics verified (`kubectl top pods`)
- Prerequisite metric layer established for future HPA baseline

### Phase 5B: Kubernetes HPA Baseline Manifests (Implemented - Statically Validated, Not Live)
- `autoscaling/v2` HorizontalPodAutoscaler manifest and Kustomize overlay (`kubernetes/hpa/`)
- Explicit scale target: `Deployment/kubepredict-workload-api` in `kubepredict-system`
- Replica range: `minReplicas: 1`, `maxReplicas: 6` (scale-to-zero forbidden)
- Resource metric: CPU target `averageUtilization: 60` relative to `100m` request
- Explicit scale-up behavior: 0s stabilization, `selectPolicy: Max` (+2 pods or +100% per 60s)
- Explicit scale-down behavior: 300s stabilization, `selectPolicy: Max` (-1 pod or -50% per 60s)
- HPA management and inspection scripts (`deploy-hpa.ps1`, `inspect-hpa.ps1`, `remove-hpa.ps1`)
- Unit and manifest test suite (`tests/unit/test_hpa_manifests.py`)
- *Runtime State:* Fixed-replica Deployment active (2 Ready pods); no live HPA object created.

### Phase 5C: HPA Baseline Live Activation & Runtime Validation (Implemented & Runtime-Verified)
- Live HPA deployment to `kubepredict-system` targeting `kubepredict-workload-api`
- Metric resolution confirmed from `metrics.k8s.io`
- Bounded controlled CPU load validated scale-up from 2 to 6 replicas (all pods Ready, 0 restarts)
- Scale-down observed following 300s stabilization window (6 -> 4 -> 3 -> 2 replicas)
- Live HPA removed and declarative 2-replica fixed baseline restored

### Phase 6A: Prometheus Server Deployment & Scrape Validation (Implemented & Runtime-Verified)
- Dedicated monitoring namespace `kubepredict-monitoring`
- Lightweight Prometheus server (`prom/prometheus:v2.54.1`) deployed with 1 replica
- Kubernetes ServiceAccount and read-only discovery ClusterRole (`kubepredict-prometheus-discovery`)
- In-cluster Kubernetes pod service discovery scraping `kubepredict-workload` (15s interval) and self-monitoring
- Exactly 2 workload scrape targets reporting health `up` with 0 scrape errors
- Core PromQL metrics verified (`up`, `apa_application_info`, `apa_http_requests_total`, `apa_work_requests_total`, etc.)
- Ephemeral emptyDir storage (1Gi) with 6h retention

### Phase 6B: Grafana Visualization & Dashboard Provisioning (Implemented & Runtime-Verified)
- Official Grafana OSS (`grafana/grafana:11.2.0`) deployed to `kubepredict-monitoring` with 1 replica
- Zero-privilege container profile: non-root UID 472, read-only rootfs, dropped ALL capabilities, RuntimeDefault seccomp, ephemeral emptyDir storage
- Runtime administrator credentials generated and managed via Kubernetes Secret (`grafana-admin-credentials`)
- Declarative Prometheus datasource provisioning (`kubepredict-prometheus`) via ConfigMap (`proxy` access mode)
- Declarative dashboard provider and version-controlled KubePredict Overview dashboard (`kubepredict-overview.json`) with 18 panels across 5 functional rows
- All 16 PromQL metrics queries validated directly and via Grafana proxy against Prometheus
- Bounded synthetic workload requests verified on dashboard panels
- Grafana ClusterIP Service on port 3000 accessible via localhost port-forward

### Phase 7A: Reproducible Request Scenario Framework (Implemented & Validated)
- Deterministic, configuration-driven request scenario framework built on Locust (`load_testing/`)
- Strict Pydantic models for stage schedules, user caps (<=50), spawn rates (<=10/s), bounded runtimes (<=900s), and localhost safety
- Five deterministic scenario profiles: `steady`, `ramp`, `periodic`, `sudden_increase`, `drift`
- Headless, bounded CLI runner (`load_testing.runner`) with stage scheduling and run metadata logging (`metadata.json`)
- Offline dry-run scenario validation (`validate-scenarios.ps1`)
- Bounded cluster smoke test execution (`smoke-test.ps1`)
- Isolated, git-ignored output directory tree (`experiments/output/<run-id>/`)
- *Status:* Implemented and verified; not yet used for formal comparative experiments.

### Phase 7B: Kubernetes Scenario Validation & Telemetry Dataset Capture (Implemented & Validated)
- Sequentially validated the 5 reproducible request scenarios (`steady`, `ramp`, `periodic`, `sudden_increase`, `drift`) against the live Kubernetes Service
- Executed 58,559 requests across all scenarios with 0 HTTP errors and 0 dropped connections
- Workload maintained under fixed 2-replica baseline (`kubepredict-workload-api` in `kubepredict-system`) with no live HPA or predictive controller
- Automated telemetry capture pipeline querying Prometheus (`experiments/telemetry/prometheus_client.py`) with 15s aligned step grid
- Kubernetes cluster telemetry collector (`experiments/telemetry/collector.py`) capturing deployment replicas, pod readiness/restarts, and `kubectl top` resource usage
- Rigorous dataset validation (`experiments/telemetry/validation.py`) checking file structure, column schemas, strictly monotonic UTC timestamps, non-negative rates, config hash stability, and replica invariants
- Generated validated datasets and metadata index under `data/raw/scenarios/` (strictly git-ignored)
- Documented schema, storage structure, and validation rules in `docs/TELEMETRY_DATASET.md`

### Phase 8A: Offline Forecasting Baselines & Chronological Evaluation (Implemented & Validated)
- Model-independent forecasting interface ([`BaseForecaster`](file:///forecasting/base.py)) with strict input validation, non-negative prediction clipping, and zero network/cluster dependencies
- Four forecasting baseline families implemented: Persistence (`PersistenceForecaster`), Trailing Moving Average (`MovingAverageForecaster`), EWMA (`EWMAForecaster`), and Trailing Linear Trend (`LinearTrendForecaster`)
- Preprocessing and chronological split engine ([`forecasting/preprocessing.py`](file:///forecasting/preprocessing.py)) with 60% train, 20% validation, and 20% test splits; zero future data leakage
- Rolling-origin evaluation engine ([`forecasting/rolling_evaluation.py`](file:///forecasting/rolling_evaluation.py)) advancing step-by-step; partition membership bounded strictly by target timestamps
- Comprehensive metrics evaluation ([`forecasting/metrics.py`](file:///forecasting/metrics.py)): MAE, RMSE, sMAPE (0-200% symmetric range), and Bias, aggregated at macro and sample-weighted levels
- Baseline comparison rule & parameter selection ([`forecasting/selection.py`](file:///forecasting/selection.py)): frozen parameters evaluated once on held-out test split; persistence retained for Horizons 1 and 2; Linear Trend (`window=8`) selected for Horizon 4
- Standalone CLI and PowerShell automation (`scripts/forecasting/`) operating completely offline without mutating Kubernetes
- Documented methodology and experimental evidence in [`docs/FORECASTING.md`](docs/FORECASTING.md)

### Phase 8B: Predictive Controllers & Comparative Experiments (Planned)
- Predictive autoscaling controller and guarded controller implementation (confidence check, burst override, drift handling, cooldown, fallback)
- Controlled execution of the 5 scenario profiles across all 4 configurations (`fixed`, `hpa`, `predictive`, `guarded`)
- Empirical performance comparison across latency, throughput, resource efficiency, and SLO compliance
- Statistical analysis of recorded telemetry and publication of benchmark evidence



