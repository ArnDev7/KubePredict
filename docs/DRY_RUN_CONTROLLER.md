# Phase 9B: Capacity Model and Dry-Run Replica Recommendations

## 1. Overview & Purpose

Phase 9B extends the Phase 9A observe-only controller with a **transparent capacity model** that converts forecasted request demand into proposed replica counts. All recommendations are recorded as structured dry-run `ScalingDecision` records without any Kubernetes mutation.

The primary objective is producing verifiable, auditable, bounded scaling recommendations that demonstrate the complete predictive → capacity → replica pipeline while preserving the strictly read-only, non-mutating safety boundary established in Phase 9A.

> [!IMPORTANT]
> **Strict Read-Only & Non-Mutation Guarantees**:
> - **Zero Kubernetes Writes**: The controller runtime contains **no** Kubernetes client, ServiceAccount, or write calls (`patch`, `update`, `scale`, `create`, `delete`, `apply`, `replace`, `edit`).
> - **Zero Live Scaling**: `ENABLE_LIVE_SCALING` remains permanently `false`. No CLI option or runtime path can enable scaling.
> - **Workload Invariant**: `kubepredict-workload-api` in `kubepredict-system` remains locked at exactly 2 ready replicas.
> - **Read-Only K8s Access**: Current replicas are read exclusively via `kubectl get deployment` subprocess calls (`shell=False`, fixed context `kind-kubepredict-dev`, fixed namespace `kubepredict-system`).
> - **Decision Confidence Deferred**: `ScalingDecision.confidence` is `None` (optional) — artificial probabilities are not fabricated prior to Phase 10.
> - **Phase 10/11 Features Deferred**: Forecast guard, workload drift detection, burst override, reactive fallback, cooldown, live actuation, and RBAC are strictly out of scope.

---

## 2. Architecture & Components

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                   KubePredict Dry-Run Controller Architecture               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌───────────────────────────┐         ┌───────────────────────────────┐   │
│   │   Prometheus Localhost    │         │     LiveTelemetryReader       │   │
│   │   (http://127.0.0.1:9090) │ ──────> │  - Validates freshness (30s)  │   │
│   │   - Fixed 15s step query  │         │  - Enforces chronological UTC │   │
│   └───────────────────────────┘         └──────────────┬────────────────┘   │
│                                                        │                    │
│   ┌───────────────────────────┐                        ▼                    │
│   │  kubectl get deployment   │         ┌───────────────────────────────┐   │
│   │  (read-only, shell=False) │ ──────> │      Dry-Run Cycle Engine     │   │
│   │  context:kind-kubepredict │         │  - Observe (Phase 9A)         │   │
│   └───────────────────────────┘         │  - Capacity Model             │   │
│                                         │  - Decision Builder           │   │
│   ┌───────────────────────────┐         └──────────────┬────────────────┘   │
│   │       ForecastStore       │                        │                    │
│   │  - Stores PendingForecast │ <────── ┤              │                    │
│   │  - Matches past targets   │         │              ▼                    │
│   └───────────────────────────┘         ┌───────────────────────────────┐   │
│                                         │     DryRunMetrics + Audit     │   │
│                                         │  - Isolated Registry          │   │
│                                         │  - http://127.0.0.1:9101      │   │
│                                         └───────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Component Details

1. **[`controller/capacity_config.py`](file:///controller/capacity_config.py)**: Pydantic v2 configuration model for capacity parameters. Enforces `extra="forbid"`, rejects booleans as numbers, validates `minimum_replicas <= maximum_replicas`, requires relative evidence reference paths.
2. **[`controller/config/capacity.yaml`](file:///controller/config/capacity.yaml)**: Versioned YAML configuration with calibrated per-replica capacity, utilization target, safety margin, replica bounds, and step limits.
3. **[`controller/capacity.py`](file:///controller/capacity.py)**: Pure deterministic capacity calculation engine. Zero I/O, zero network, zero filesystem mutation. Converts predicted request rate → bounded, step-limited replica count.
4. **[`controller/k8s_reader.py`](file:///controller/k8s_reader.py)**: Strictly read-only Kubernetes Deployment state reader via `subprocess.run()` with `shell=False` and fixed context/namespace/deployment parameters.
5. **[`controller/decision.py`](file:///controller/decision.py)**: Decision builder constructing `ScalingDecision` in `ControllerMode.DRY_RUN` with `live_scaling_enabled=False` always.
6. **[`controller/dry_run.py`](file:///controller/dry_run.py)**: Dry-run cycle executor composing Phase 9A observer logic with capacity model and decision builder.
7. **[`controller/metrics.py`](file:///controller/metrics.py)**: Extended operational metrics with dry-run recommendation counters and gauges.
8. **[`controller/audit.py`](file:///controller/audit.py)**: Extended static write-capability auditor rejecting all Kubernetes mutation commands and `shell=True`.

---

## 3. Capacity Model Specification

### 3.1 Calibration Evidence

Analysis of **74 valid intervals** across the 5 reproducible Phase 7B fixed 2-replica baseline runs (`steady`, `ramp`, `periodic`, `sudden_increase`, `drift`) where all stability invariants held:

| Statistic | Value (req/s per replica) |
|---|---|
| Min | 0.47 |
| 25th percentile | 15.73 |
| Median (p50) | 27.25 |
| 75th percentile | 36.25 |
| 90th percentile | 43.96 |
| Max | 50.19 |

**Workload Profile**: Local kind cluster, CPU iterations: 100,000, delay: 20ms, payload: 50 bytes (`/work` endpoint).

### 3.2 Configured Capacity Parameters

| Parameter | Value | Rationale |
|---|---|---|
| Base capacity | 25.0 req/s per replica | Conservative value below median observed throughput |
| Target utilization | 0.70 (70%) | Headroom to absorb load variance |
| Safety margin | 0.90 (90%) | Conservative headroom for prediction error |
| **Effective capacity** | **15.75 req/s per replica** | $25.0 \times 0.70 \times 0.90$ |
| Minimum replicas | 1 | No scale-to-zero |
| Maximum replicas | 10 | Upper bound for local kind cluster |
| Max scale-up step | +3 per cycle | Bounded change rate to prevent oscillation |
| Max scale-down step | -1 per cycle | Conservative downward change rate |
| Status | `provisional_calibrated` | Derived from Phase 7B data; saturation cliff not tested |

### 3.3 Calculation Pipeline

The capacity model is a **pure deterministic function** with the following steps:

1. **Input Validation**: Reject NaN, infinite, or negative predicted rates and invalid replica counts.
2. **Effective Capacity**: $\text{eff} = \text{base\_capacity} \times \text{target\_util} \times \text{safety\_margin}$
3. **Unconstrained Demand**:
   $$\text{unconstrained} = \lceil \text{predicted\_rate} / \text{eff} \rceil$$
   If $\text{predicted\_rate} \le \text{minimum\_prediction\_rate}$, unconstrained is 0.
4. **Minimum Bound**: $\text{proposed} = \max(\text{unconstrained}, \text{min\_replicas})$
5. **Maximum Bound**: $\text{proposed} = \min(\text{proposed}, \text{max\_replicas})$
6. **Scale-Up Step Limit**: If $\text{proposed} > \text{current}$: $\text{final} = \text{current} + \min(\text{proposed} - \text{current}, \text{max\_up\_step})$
7. **Scale-Down Step Limit**: If $\text{proposed} < \text{current}$: $\text{final} = \text{current} - \min(\text{current} - \text{proposed}, \text{max\_down\_step})$
8. **Direction**: `SCALE_UP` if $\text{final} > \text{current}$, `SCALE_DOWN` if $\text{final} < \text{current}$, else `NO_CHANGE`
9. **Reason**: Priority: step-limited → max/min bound → no change → forecast demand

### 3.4 Decision Reasons

| Reason | When Applied |
|---|---|
| `FORECAST_DEMAND` | Unconstrained demand-driven scaling, no limits hit |
| `NO_CHANGE_REQUIRED` | Final replicas equal current replicas |
| `MINIMUM_BOUND` | Proposed was clamped up to minimum replicas |
| `MAXIMUM_BOUND` | Proposed was clamped down to maximum replicas |
| `SCALE_UP_STEP_LIMITED` | Scale-up delta exceeded maximum step |
| `SCALE_DOWN_STEP_LIMITED` | Scale-down delta exceeded maximum step |

---

## 4. Read-Only Kubernetes Access

### 4.1 Deployment Reader

[`controller/k8s_reader.py`](file:///controller/k8s_reader.py) reads deployment state via:

```python
subprocess.run(
    [
        "kubectl",
        "get",
        "deployment",
        name,
        "--context",
        context,
        "--namespace",
        namespace,
        "-o",
        "json",
    ],
    shell=False,
    timeout=10.0,
    capture_output=True,
    text=True,
)
```

**Strict defaults**:
- Context: `kind-kubepredict-dev`
- Namespace: `kubepredict-system`
- Deployment: `kubepredict-workload-api`

Returns typed `DeploymentReplicaStatus` containing `available_replicas`, `desired_replicas`, `ready_replicas`, `is_valid`, and `error_message`.

### 4.2 Static Write-Capability Audit

[`controller/audit.py`](file:///controller/audit.py) scans all controller and script source files for:
- **Rejected commands**: `kubectl replace`, `kubectl create`, `kubectl edit`, `kubectl delete`, `kubectl scale`, `kubectl patch`, `kubectl apply`
- **Rejected subprocess options**: `shell=True`
- **Rejected Kubernetes client methods**: `replace_namespaced_*`, `update_namespaced_*`, `delete_namespaced_*`, `patch_namespaced_*`, dynamic clients
- **Permitted read-only commands**: `kubectl get deployment`, `kubectl get pods`, `kubectl get hpa`

---

## 5. Dry-Run Decision Records

Each dry-run recommendation produces a `ScalingDecision` record:

| Field | Value |
|---|---|
| `mode` | `ControllerMode.DRY_RUN` |
| `live_scaling_enabled` | `False` (always) |
| `confidence` | `None` (deferred to Phase 10) |
| `action_executed` | `False` (always) |
| `current_replicas` | From `kubectl get deployment` |
| `proposed_replicas` | Post-bounds, pre-step-limit |
| `final_replicas` | Post-step-limit |
| `direction` | `SCALE_UP` / `SCALE_DOWN` / `NO_CHANGE` |
| `reason` | One of 6 `DecisionReason` values |
| `details` | Full calculation breakdown |

---

## 6. Controller Operational Metrics

Phase 9B adds the following metrics to the existing Phase 9A observer metrics on `http://127.0.0.1:9101/metrics`:

| Metric Name | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `kubepredict_dry_run_recommendations_total` | Counter | `direction`, `reason` | Dry-run recommendations by direction and reason |
| `kubepredict_dry_run_current_replicas` | Gauge | None | Current replicas read from deployment |
| `kubepredict_dry_run_proposed_replicas` | Gauge | None | Proposed replicas after bounds |
| `kubepredict_dry_run_final_replicas` | Gauge | None | Final replicas after step limits |
| `kubepredict_dry_run_predicted_request_rate` | Gauge | None | Predicted request rate from forecaster |
| `kubepredict_dry_run_effective_capacity_per_replica` | Gauge | None | Effective capacity per replica |
| `kubepredict_dry_run_constraint_applications_total` | Counter | `constraint` | Constraint application events |

*Label Cardinality Guarantee:* Fixed, low-cardinality label sets only (directions: 3 values, reasons: 6 values, constraints: 5 values).

---

## 7. Generated Evidence Directory Structure

Each dry-run execution records complete audit evidence under `experiments/output/controller-dry-run/<run-id>/`:
```
experiments/output/controller-dry-run/<run-id>/
    metadata.json            # Run parameters, hashes, status, summary counts
    cycles.jsonl             # Per-cycle telemetry status, forecast, and recommendation
    recommendations.csv      # All scaling recommendations with full calculation details
    skipped_cycles.csv       # Skipped cycles and documented reasons
    validation_report.json   # Contract compliance and data quality report
    execution.log            # Complete chronological execution logs
```
All generated outputs are ignored by Git.

---

## 8. Operations & CLI Usage

### 8.1 Validate Configuration & Capacity Model (Offline)
```powershell
.\scripts\controller\validate-capacity-model.ps1
# or via CLI:
python -m controller.cli validate --config config/observer.yaml --frozen-forecaster forecasting/config/final_forecaster.yaml --capacity-config controller/config/capacity.yaml
```

### 8.2 Run Bounded Dry-Run Controller (Live Localhost)
```powershell
.\scripts\controller\run-dry-run.ps1 -MaxCycles 12
# or via CLI:
python -m controller.cli dry-run --config config/observer.yaml --frozen-forecaster forecasting/config/final_forecaster.yaml --capacity-config controller/config/capacity.yaml --max-cycles 12
```

### 8.3 Inspect Dry-Run Artifacts
```powershell
.\scripts\controller\inspect-dry-run.ps1 -RunDir experiments/output/controller-dry-run/<run-id>
```

---

## 9. Limitations & Scope Boundary

- **No Forecast Guard**: Predictions are not filtered by confidence intervals or guard conditions.
- **No Workload Drift Detection**: No drift-aware capacity recalibration.
- **No Burst Override**: No reactive override for sudden spike detection.
- **No Reactive Fallback**: If telemetry is stale or missing, the controller records skipped cycles without falling back to reactive CPU metrics.
- **No Cooldown / Stabilization**: No inter-cycle stabilization window for scaling decisions.
- **No Live Scaling**: Zero Kubernetes write access; decisions are logged only.
- **Provisional Calibration**: Per-replica capacity is calibrated from Phase 7B fixed-baseline data; saturation cliff testing was not conducted.
- **Pure In-Process Controller**: Not packaged as a container or deployed to Kubernetes in Phase 9B.

---

## 10. Live Verification & Execution Results

Live cluster dry-run verification was executed against local cluster `kind-kubepredict-dev`:

| Parameter | Value |
|---|---|
| **Run ID** | `dry-run-20261027_105511` |
| **Status** | `completed` |
| **Output Directory** | `experiments/output/controller-dry-run/dry-run-20261027_105511` |
| **Cycles Executed** | 12 / 12 (166.7s elapsed) |
| **Forecasts Created** | 12 |
| **Forecasts Matched** | 11 |
| **Forecasts Pending** | 1 (12th cycle target timestamp in future at completion) |
| **Forecasts Expired** | 0 |
| **Recommendations Created** | 12 |
| **Skipped Cycles** | 0 |
| **Current Replicas Read** | 2 (read-only via `kubectl get deployment`, shell=False) |
| **Effective Capacity Per Replica** | 15.75 req/s ($25.0 \times 0.70 \times 0.90$) |
| **Recommendation Directions** | 12 `scale_down` (natural idle traffic ~0.71-0.76 req/s -> 1 replica) |
| **Recommendation Reasons** | 12 `forecast_demand` |
| **All action_executed Values** | `false` across all 12 rows (strictly enforced) |
| **Zero Kubernetes Writes** | `true` (no K8s client, no mutation calls, generation 6 unchanged) |
| **Zero Kubernetes Mutations** | `true` (workload remained locked at 2 ready replicas) |
| **Metrics Endpoint** | `http://127.0.0.1:9101/metrics` verified HTTP 200 with recommendation metrics |
| **Temporary Process Cleanup** | Prometheus port-forward (PID: 18340) stopped; ports 9090 & 9101 released |
