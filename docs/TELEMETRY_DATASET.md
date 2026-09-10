# Phase 7B: Kubernetes Scenario Validation and Telemetry Dataset Capture

## 1. Overview & Purpose

Phase 7B validates the five reproducible request scenarios developed in Phase 7A (`steady`, `ramp`, `periodic`, `sudden_increase`, `drift`) against the deployed `kubepredict-workload-api` Service in the `kubepredict-dev` cluster under the fixed two-replica baseline.

The primary deliverable of this phase is a clean, aligned, and structurally validated raw telemetry dataset saved in `data/raw/scenarios/`. This dataset provides historical time series for offline forecasting model evaluation, feature engineering, and training in subsequent phases (Phase 8A).

> [!IMPORTANT]
> **Non-Benchmark Classification**: The telemetry collected in Phase 7B is strictly for scenario validation and offline model input. It does **not** evaluate autoscaling performance, capacity limits, or controller effectiveness. No comparative performance or latency conclusions are drawn.

---

## 2. Distinction Between Scenario Validation and Formal Experiments

| Dimension | Phase 7B Scenario Validation | Formal Comparative Experiments (Phase 8) |
| :--- | :--- | :--- |
| **Objective** | Validate scenario runner on real cluster and capture training telemetry | Empirically compare autoscaling controllers under identical load |
| **Active Controller** | Fixed 2-replica baseline only (no HPA, no predictive controller) | Matrix of controllers (`fixed`, `hpa`, `predictive`, `guarded`) |
| **Autoscaling** | Strictly forbidden; verified replica invariant = 2 | Active; controller modifies replica counts dynamically |
| **Data Usage** | Training and evaluation input dataset for forecasting | Evidence for research conclusions, hypothesis validation, and SLO analysis |
| **Repetitions** | 1 sequential execution per scenario profile | Multiple repeated statistical trials per profile and controller |

---

## 3. Data Sources & Architecture

The telemetry pipeline aggregates metrics across three distinct project layers:

1. **Prometheus Server (`prom/prometheus:v2.54.1`)**:
   - In-cluster service discovery scrapes both workload pods (`10.244.0.5:8000`, `10.244.0.6:8000`) every 15 seconds.
   - Queried via `/api/v1/query_range` using the typed [`PrometheusClient`](file:///experiments/telemetry/prometheus_client.py) over a local port-forward (`127.0.0.1:19090`).
2. **Kubernetes Workload Status & Metrics Server (`metrics.k8s.io`)**:
   - Sampled by the read-only [`KubernetesCollector`](file:///experiments/telemetry/collector.py) via `kubectl`.
   - Deployment desired, available, and ready replicas.
   - Per-pod readiness, phase, and restart counts.
   - Per-pod CPU millicores and memory bytes via `kubectl top pods`.
3. **Locust Scenario Runner (`load_testing/`)**:
   - High-resolution request and failure summaries (`locust_stats.csv`, `locust_stats_history.csv`, `locust_failures.csv`).
   - Ground-truth stage schedule mapping elapsed runtime to configured target users and spawn rates.

---

## 4. Query Specification (`experiments/configs/telemetry_queries.yaml`)

Every PromQL query is version-controlled and verified:

| Field Name | Source | PromQL Expression | Unit | Required |
| :--- | :---: | :--- | :---: | :---: |
| `request_rate_per_second` | Prometheus | `sum(rate(apa_http_requests_total{job="kubepredict-workload"}[1m]))` | req/s | Yes |
| `completed_work_rate_per_second` | Prometheus | `sum(rate(apa_work_requests_total{job="kubepredict-workload",outcome="completed"}[1m]))` | req/s | Yes |
| `http_5xx_rate_per_second` | Prometheus | `sum(rate(apa_http_requests_total{job="kubepredict-workload",status_code=~"5.."}[1m]))` | req/s | Yes |
| `rejected_work_rate_per_second` | Prometheus | `sum(rate(apa_work_requests_total{job="kubepredict-workload",outcome="rejected"}[1m]))` | req/s | No |
| `p95_http_duration_seconds` | Prometheus | `histogram_quantile(0.95, sum by (le) (rate(apa_http_request_duration_seconds_bucket{job="kubepredict-workload"}[5m])))` | seconds | Yes |
| `average_http_duration_seconds` | Prometheus | `sum(rate(apa_http_request_duration_seconds_sum[1m])) / sum(rate(apa_http_request_duration_seconds_count[1m]))` | seconds | Yes |
| `requests_in_progress` | Prometheus | `sum(apa_http_requests_in_progress{job="kubepredict-workload"})` | requests | Yes |
| `cpu_iterations_rate_per_second` | Prometheus | `sum(rate(apa_cpu_iterations_total{job="kubepredict-workload"}[1m]))` | iters/s | Yes |
| `configured_delay_rate_seconds_per_second`| Prometheus | `sum(rate(apa_artificial_delay_seconds_total{job="kubepredict-workload"}[1m]))` | seconds/s| Yes |
| `prometheus_targets_up` | Prometheus | `sum(up{job="kubepredict-workload"})` | targets | Yes |
| `pod_http_request_rate_per_second` | Prometheus | `sum by (pod) (rate(apa_http_requests_total{job="kubepredict-workload"}[1m]))` | req/s | Yes |
| `pod_work_request_rate_per_second` | Prometheus | `sum by (pod) (rate(apa_work_requests_total{job="kubepredict-workload",outcome="completed"}[1m]))` | req/s | Yes |

---

## 5. Dataset Schema & Structure

Datasets are stored in directories under `data/raw/scenarios/<scenario-name>/<run-id>/`:

```text
data/raw/scenarios/
├── dataset-index.json
└── steady/
    └── run-steady-20260910_154638/
        ├── scenario_config.yaml         # Exact byte-for-byte copy of executed scenario
        ├── metadata.json                # Complete audit record and configuration hashes
        ├── prometheus_series.json       # Raw JSON response from Prometheus range queries
        ├── kubernetes_samples.json      # Raw snapshots from Kubernetes API / metrics server
        ├── telemetry.csv                # Normalized, aligned system-level time series
        ├── pod_telemetry.csv            # Normalized per-pod time series
        ├── locust_stats.csv             # Locust aggregate statistics summary
        ├── locust_stats_history.csv     # Locust per-second stats time series
        ├── locust_failures.csv          # Locust error records (0 failures recorded)
        ├── validation_report.json       # Comprehensive validation report
        └── run.log                      # Lifecycle execution log
```

### System Telemetry Schema (`telemetry.csv`)
- `timestamp_utc`: ISO-8601 UTC timestamp.
- `run_id`: Unique run identifier.
- `scenario_name`: Scenario profile slug (`steady`, `ramp`, `periodic`, `sudden_increase`, `drift`).
- `elapsed_seconds`: Seconds since scenario execution started.
- `scenario_stage`: Active stage label (`stage_1`, `stage_2`, etc.).
- `configured_target_users`: Concurrency target defined in the profile.
- `configured_spawn_rate_per_second`: Spawn rate defined in the profile.
- `request_rate_per_second`: Total HTTP incoming request rate.
- `completed_work_rate_per_second`: Completed synthetic work rate.
- `http_5xx_rate_per_second`: Server error rate (empty if 0 errors).
- `rejected_work_rate_per_second`: Rate of bounds-rejected requests.
- `p95_http_duration_seconds`: 95th percentile response duration.
- `average_http_duration_seconds`: Average response duration over 1-minute window.
- `requests_in_progress`: In-flight concurrency gauge.
- `cpu_iterations_rate_per_second`: Rate of CPU hashing iterations.
- `configured_delay_rate_seconds_per_second`: Rate of artificial sleep delay.
- `prometheus_targets_up`: Count of healthy scrape targets (verified 2.0).
- `deployment_desired_replicas`: Always 2 (fixed baseline invariant).
- `deployment_available_replicas`: Always 2.
- `deployment_ready_replicas`: Always 2.
- `source_interval_seconds`: Nominal sampling step (15.0s).

### Pod Telemetry Schema (`pod_telemetry.csv`)
- `timestamp_utc`: ISO-8601 UTC timestamp.
- `pod`: Pod name (e.g. `kubepredict-workload-api-7649cd9b6-czpjm`).
- `request_rate_per_second`: Pod-specific request rate.
- `work_rate_per_second`: Pod-specific work rate.
- `cpu_millicores`: Pod CPU usage from `kubectl top`.
- `memory_bytes`: Pod memory usage from `kubectl top`.
- `ready`: Pod readiness boolean (`true`/`false`).
- `restart_count`: Cumulative restart counter (verified 0).

---

## 6. Telemetry Sampling & Alignment Policy

- **Sampling Interval**: 15.0 seconds, exactly matching the Prometheus scrape interval (`scrape_interval: 15s`).
- **Timestamp Ordering**: All samples are strictly monotonically increasing in UTC. Duplicate timestamps within a run are strictly rejected.
- **Missing Data Policy**: If a metric has no activity (e.g. `http_5xx_rate_per_second` when 0 errors occur), the cell is preserved as an empty string. Fabricating synthetic zeroes or forward-filling without provenance is strictly prohibited.
- **Floating-Point Handling**: NaN and Inf strings from Prometheus are safely parsed and preserved as explicit missing values rather than invalid strings.

---

## 7. Execution Order & Operational Safeguards

The five scenarios are executed sequentially using [`scripts/experiments/capture-all-scenarios.ps1`](file:///scripts/experiments/capture-all-scenarios.ps1):

1. `steady` (180s duration + 20s scrape buffer)
2. *45s recovery interval*
3. `ramp` (240s duration + 20s scrape buffer)
4. *45s recovery interval*
5. `periodic` (240s duration + 20s scrape buffer)
6. *45s recovery interval*
7. `sudden_increase` (180s duration + 20s scrape buffer)
8. *45s recovery interval*
9. `drift` (240s duration + 20s scrape buffer)

### Safety Rules Enforced During Execution
- **Context Enforcement**: Refuses execution if current context is not `kind-kubepredict-dev`.
- **HPA Guard**: Prohibits execution if any HorizontalPodAutoscaler exists in the cluster.
- **Replica Invariant**: Blocks execution if workload deployment desired or ready replicas differ from 2.
- **Process Cleanup**: Manages temporary `kubectl port-forward` PIDs in `try...finally` blocks; verifies socket release on ports 18000 and 19090 before and after each run.
- **Git Hygiene**: `data/raw/*` is completely ignored by Git while `!data/raw/.gitkeep` is tracked.

---

## 8. Captured Datasets Summary (Phase 7B Results)

| Scenario | Run ID | Duration | Requests | Failures | System Samples | Pod Samples | Status |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **`steady`** | `run-steady-20260910_154638` | 180s | 9,499 | 0 | 15 | 30 | **VALID** |
| **`ramp`** | `run-ramp-20260910_155057` | 240s | 14,850 | 0 | 19 | 38 | **VALID** |
| **`periodic`** | `run-periodic-20260910_155924` | 240s | 10,726 | 0 | 19 | 32 | **VALID** |
| **`sudden_increase`** | `run-sudden_increase-20260910_160442` | 180s | 10,273 | 0 | 15 | 30 | **VALID** |
| **`drift`** | `run-drift-20260910_160901` | 240s | 13,211 | 0 | 19 | 38 | **VALID** |
| **Total** | **5 Valid Datasets** | **1,080s** | **58,559** | **0** | **87** | **168** | **ALL VALID** |

---

## 9. Helper Scripts

```powershell
# Validate query specification and plan offline
.\scripts\experiments\validate-telemetry-plan.ps1

# Execute single scenario capture
.\scripts\experiments\capture-scenario.ps1 -Scenario steady

# Execute all 5 scenarios sequentially with recovery intervals
.\scripts\experiments\capture-all-scenarios.ps1 -RecoveryIntervalSeconds 45

# Inspect a generated dataset
.\scripts\experiments\inspect-dataset.ps1 -DatasetPath data/raw/scenarios/steady/run-steady-20260910_154638
```

---

## 10. Limitations & Scope Affirmation

- **No Predictive Forecasting**: No forecasting models (ARIMA, EWMA, Prophet, LSTM) have been trained or deployed.
- **No Controller Evaluation**: No autoscaling controller was active during data capture; the system remained strictly in the fixed 2-replica baseline.
- **Input Data Only**: The captured datasets serve exclusively as ground-truth inputs for future offline forecasting development.
