# Phase 9A: Live Telemetry Reader and Observe-Only Forecasting Controller

## 1. Overview & Purpose

Phase 9A connects the frozen offline forecaster from Phase 8B to live Prometheus telemetry through a strictly read-only, observe-only controller loop.

The primary objective is connecting real-time workload metrics to the `ForecastingService`, validating telemetry freshness, generating point forecasts at the selected default horizon, registering pending predictions, matching them against later actual observations, and computing residual errors as empirical evidence for future safety guard design (Phase 9B/10).

> [!IMPORTANT]
> **Strict Observe-Only Scope & Non-Mutation Guarantees**:
> - **Zero Scaling Decisions**: The controller does **not** calculate desired replicas, capacity models, or scaling directions.
> - **Zero Kubernetes Writes**: The controller runtime contains **no** Kubernetes client, ServiceAccount, or write calls (`patch`, `update`, `scale`, `create`, `delete`).
> - **Zero Live Scalers**: Horizontal Pod Autoscaler (HPA) remains inactive; workload remains in the fixed 2-replica baseline.
> - **Distinction of Fallbacks**: `fallback_used` in Phase 9A refers **strictly** to the internal `ForecastingService` falling back to Persistence due to insufficient observation history. It does **not** represent a reactive autoscaling fallback.

---

## 2. Architecture & Components

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       KubePredict Observe-Only Architecture                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌───────────────────────────┐         ┌───────────────────────────────┐   │
│   │   Prometheus Localhost    │         │     LiveTelemetryReader       │   │
│   │   (http://127.0.0.1:9090) │ ──────> │  - Validates freshness (30s)  │   │
│   │   - Fixed 15s step query  │         │  - Enforces chronological UTC │   │
│   └───────────────────────────┘         │  - Rejects NaN/Inf/Negative   │   │
│                                         └──────────────┬────────────────┘   │
│                                                        │                    │
│                                                        ▼                    │
│   ┌───────────────────────────┐         ┌───────────────────────────────┐   │
│   │       ForecastStore       │         │        Observer Cycle         │   │
│   │  - Stores PendingForecast │ <────── │  - Constructs ForecastInput   │   │
│   │  - Matches past targets   │         │  - Invokes ForecastService    │   │
│   │  - Calculates residuals   │         │  - Emits ObserverCycleResult  │   │
│   └───────────────────────────┘         └──────────────┬────────────────┘   │
│                                                        │                    │
│                                                        ▼                    │
│                                         ┌───────────────────────────────┐   │
│                                         │        ObserverMetrics        │   │
│                                         │  - Isolated Registry          │   │
│                                         │  - http://127.0.0.1:9101      │   │
│                                         └───────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Component Details
1. **`controller/config.py`**: Pydantic configuration model enforcing `mode="observe"`, localhost Prometheus target, fixed query, bounded cycles, and forbidden unknown fields.
2. **`controller/telemetry.py`**: Live telemetry reader querying recent history over `history_window_seconds`, sorting chronologically, detecting missing intervals and staleness.
3. **`controller/forecast_store.py`**: In-memory forecast registry tracking pending forecasts, matching actuals once target timestamps occur, and computing signed residuals.
4. **`controller/metrics.py`**: Isolated Prometheus registry exposing 10 operational metrics with strictly bounded label cardinality.
5. **`controller/observer.py`**: The pure observation cycle orchestrating telemetry ingestion, matching, forecast generation, and metric updates.
6. **`controller/audit.py`**: Automated static write-capability auditor scanning AST and script syntax to verify zero Kubernetes write capability.
7. **`controller/cli.py`**: Command-line interface providing `validate`, `observe`, and `audit-source` modes.

---

## 3. Telemetry Contract & Query Specification

- **Target Variable**: `request_rate_per_second`
- **Fixed Version-Controlled PromQL Query**:
  ```promql
  sum(rate(apa_http_requests_total{job="kubepredict-workload"}[1m]))
  ```
  *(Arbitrary PromQL is strictly forbidden via configuration validation).*
- **Sampling Interval**: 15.0 seconds (matches Prometheus scrape and frozen model).
- **History Window**: 180.0 seconds (retrieves up to 12 historical steps).
- **Freshness Criterion**: Telemetry is considered fresh if the age of the latest observation $t_{\text{now}} - t_{\text{latest}} \le 30.0\,\text{s}$. If older, the cycle outcome is `forecast_skipped_stale_telemetry`.
- **Missing Data Policy**: Missing intervals are counted but never imputed as artificial zeros or interpolated. If historical sample count $< \text{minimum\_history}$, the cycle outcome is `forecast_skipped_insufficient_history`.

---

## 4. Frozen Forecasting Integration & Matching Logic

### 4.1 Frozen Configuration
- **Model**: `persistence`
- **Default Horizon**: $h=1$ step (15.0 seconds)
- **Status**: `offline_selected` (verified from [`forecasting/config/final_forecaster.yaml`](file:///forecasting/config/final_forecaster.yaml))
- **Minimum History**: 1 sample

### 4.2 Pending Forecast Lifecycle
1. **Creation**: When telemetry is fresh and sufficient, `ForecastInput` is constructed and passed to `ForecastingService.predict()`.
2. **Registration**: The resulting `ForecastOutput` is assigned a unique `forecast_id` and registered in `ForecastStore` with target timestamp $t_{\text{target}} = t_{\text{gen}} + 15.0\,\text{s}$ and status `pending`.
3. **Delayed Matching**: On subsequent cycles where $t_{\text{current}} \ge t_{\text{target}}$, the store searches available telemetry for the observation closest to $t_{\text{target}}$ within tolerance (15.0s). Future observations are never accessed prematurely.
4. **Residual Calculation**:
   $$\text{residual} = y_{\text{actual}} - \hat{y}_{\text{predicted}}$$
   $$\text{absolute\_error} = |y_{\text{actual}} - \hat{y}_{\text{predicted}}|$$
   The forecast status transitions to `matched`.
5. **Expiration**: Unmatched pending forecasts whose target time has elapsed beyond 300 seconds are transitioned to `expired`.

---

## 5. Controller Operational Metrics

Metrics are exposed on `http://127.0.0.1:9101/metrics` using an isolated `CollectorRegistry`:

| Metric Name | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `kubepredict_observer_cycles_total` | Counter | `outcome` | Total execution cycles by outcome enum |
| `kubepredict_observer_telemetry_age_seconds` | Gauge | None | Age of latest telemetry observation |
| `kubepredict_observer_history_observations` | Gauge | None | Valid observations in current window |
| `kubepredict_observer_forecasts_total` | Counter | `model`, `fallback_used` | Point forecasts generated |
| `kubepredict_observer_prediction_request_rate` | Gauge | None | Most recent predicted request rate |
| `kubepredict_observer_actual_request_rate` | Gauge | None | Most recent matched actual request rate |
| `kubepredict_observer_absolute_error` | Gauge | None | Absolute residual of last matched forecast |
| `kubepredict_observer_pending_forecasts` | Gauge | None | Count of forecasts awaiting actuals |
| `kubepredict_observer_matched_forecasts_total` | Counter | None | Cumulative matched forecasts |
| `kubepredict_observer_expired_forecasts_total` | Counter | None | Cumulative expired forecasts |

*Label Cardinality Guarantee:* Zero high-cardinality labels (no forecast IDs, request IDs, timestamps, or raw query text).

---

## 6. Generated Evidence Directory Structure

Each observer run records complete audit evidence under `experiments/output/controller-observe/<run-id>/`:
```
experiments/output/controller-observe/<run-id>/
    metadata.json            # Run parameters, hashes, status, summary counts
    cycles.jsonl             # Per-cycle telemetry status and duration
    forecasts.csv            # All generated point forecasts and resolution
    matched_residuals.csv    # Target timestamps, predictions, actuals, residuals
    skipped_cycles.csv       # Skipped forecast cycles and documented reasons
    validation_report.json   # Contract compliance and data quality report
    execution.log            # Complete chronological execution logs
```
All generated outputs are ignored by Git.

---

## 7. Operations & CLI Usage

### 7.1 Validate Configuration & Static Write Audit (Offline)
```powershell
.\scripts\controller\validate-observer.ps1
# or via CLI:
python -m controller.cli validate --config config/observer.yaml --frozen-forecaster forecasting/config/final_forecaster.yaml
```

### 7.2 Run Bounded Observe-Only Controller (Live Localhost)
```powershell
.\scripts\controller\run-observer.ps1 -MaximumCycles 12
# or via CLI:
python -m controller.cli observe --config config/observer.yaml --frozen-forecaster forecasting/config/final_forecaster.yaml --max-cycles 12
```

### 7.3 Inspect Run Artifacts
```powershell
.\scripts\controller\inspect-observer-run.ps1 -RunDir experiments/output/controller-observe/<run-id>
```

---

## 8. Limitations & Scope Boundary

- **No Scaling Recommendations**: Does not output target pod counts, scale steps, or scaling directions.
- **No Safety Guards**: Does not filter predictions by confidence, detect burst spikes, or suppress oscillations.
- **No Reactive Fallback**: If telemetry is stale or missing, the controller records skipped cycles without falling back to reactive CPU metrics.
- **Pure In-Process Controller**: Not packaged as a container or deployed to Kubernetes in Phase 9A.
