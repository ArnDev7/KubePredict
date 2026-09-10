# Phase 8B: Forecasting Audit, Robustness Analysis, and Final Model Selection

## Executive Summary

Phase 8B executes an independent audit of the offline forecasting pipeline and baseline results generated in Phase 8A (`eval-20260910_180143`), verifies the complete absence of temporal leakage, evaluates model robustness across all five Kubernetes request scenarios, analyzes parameter sensitivity, and freezes defensible forecasting configurations for subsequent controller integration.

**Status:** `offline_selected` — **NOT** `production_ready`.
The selected models and configurations are validated strictly offline against captured Phase 7B telemetry datasets. Live controller integration, Prometheus query adapters, replica calculation, and scaling actuators are deferred to subsequent phases.

All evaluations and audits were executed with **zero Kubernetes cluster mutation**: no pods, services, HPA, Prometheus, or Grafana resources were altered. Raw Phase 7B telemetry files remained 100% read-only and immutably preserved.

---

## 1. Evaluated Telemetry Datasets and Horizons

### 1.1 Evaluated Scenarios & Sample Counts
The evaluation and audit operate over 5 reproducible request scenarios captured under fixed 2-replica baseline in Phase 7B:

| Scenario Name | Description | Total Samples | Sampling Interval | Train Partition (70%) | Val Partition (15%) | Test Partition (15%) | Telemetry File SHA-256 (Prefix) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `steady` | Constant 20 req/s load | 19 | 15.0s | 13 | 3 | 3 | `3ce1a5e1e5ca...` |
| `ramp` | Linear increase from 5 to 50 req/s | 17 | 15.0s | 11 | 3 | 3 | `81d4bfe8900f...` |
| `periodic` | Sine wave oscillating 10–40 req/s | 17 | 15.0s | 11 | 3 | 3 | `7bf749ea8361...` |
| `sudden_increase` | Step jump from 10 to 60 req/s | 15 | 15.0s | 9 | 3 | 3 | `f9de64c3c3a9...` |
| `drift` | Gradual baseline drift with noise | 17 | 15.0s | 11 | 3 | 3 | `b54203657cb2...` |

### 1.2 Evaluated Forecast Horizons
Three distinct forecast horizons were evaluated, matching physical autoscaling control constraints:
1. **Horizon 1 ($h=1$, 15 seconds):** Exactly matches the Prometheus scrape interval and single controller evaluation cycle.
2. **Horizon 2 ($h=2$, 30 seconds):** Evaluates short-term proactive scaling readiness.
3. **Horizon 4 ($h=4$, 60 seconds):** Evaluates lead time sufficient for container image pulls and cold-pod scheduling.

---

## 2. Audit Methodology and Leakage Verification

The Phase 8B audit engine (`forecasting/audit.py`) subjected the Phase 8A evaluation artifacts (`eval-20260910_180143`) to clean-room verification.

### 2.1 Data Immutability
- All 5 raw telemetry CSV files and `data/raw/scenarios/dataset-index.json` (SHA-256: `7a5fd04383a1dacbfc6bd9f489b6eaf27f5d740cb15584308ad2b78b24ee3ab0`) were hashed and verified against the hashes recorded in Phase 8A `run_metadata.json`.
- Status: **PASS** (100% bitwise match).

### 2.2 Chronological Split & Monotonicity Audit
- Verified partition order: $\max(\text{train}) < \min(\text{val}) \le \max(\text{val}) < \min(\text{test})$.
- Monotonic timestamps: zero negative intervals, zero duplicate timestamps, and zero time gaps across all 5 scenario series.
- Status: **PASS**.

### 2.3 Forecast Origin & Target Alignment Audit
- For every recorded forecast origin $t_{\text{origin}}$, all input observations satisfy $t_{\text{obs}} \le t_{\text{origin}}$.
- Targets are strictly $t_{\text{target}} = t_{\text{origin}} + h \times 15.0\,\text{s}$.
- Inspected the first origin, last origin, and random internal origins per scenario: zero post-origin data was visible to any forecaster.
- Status: **PASS**.

### 2.4 Leakage Trap Test Suite
Six behavioral leakage traps were implemented in `tests/unit/test_forecasting_leakage.py` and executed dynamically:
1. **Future Perturbation Trap:** Modifying future observations post-origin caused zero variation in predictions for Persistence, Moving Average, EWMA, and Linear Trend.
2. **Split Perturbation Trap:** Altering held-out test split data produced zero change in validation parameter selections.
3. **Target Column Trap:** API contracts strictly decouple input observation vectors from future ground-truth actuals.
4. **Timestamp Trap:** Target offsets strictly adhere to interval arithmetic without timestamp interpolation or time-travel distortion.
5. **Scenario Boundary Trap:** Confirmed partitions and models never blend observations across scenario boundaries.
6. **Preprocessing Fit Trap:** Preprocessing functions do not maintain global state or compute dataset-wide statistics across splits.
- Status: **PASS** (All 6 traps passed).

---

## 3. Reproducibility and Independent Metric Verification

### 3.1 Clean-Room Mathematical Metric Recomputation
An independent auditor recomputed MAE, RMSE, sMAPE, and bias directly from the prediction actual pairs recorded in `data/processed/forecasting/eval-20260910_180143/evaluation_predictions.csv`:
- **MAE:** $\frac{1}{N}\sum |y_t - \hat{y}_t|$
- **RMSE:** $\sqrt{\frac{1}{N}\sum (y_t - \hat{y}_t)^2}$
- **sMAPE:** $\frac{100\%}{N}\sum \frac{2|y_t - \hat{y}_t|}{|y_t| + |\hat{y}_t|}$ (with $0/0 \to 0$)
- **Bias:** $\frac{1}{N}\sum (\hat{y}_t - y_t)$

All recomputed metrics matched the stored Phase 8A `evaluation_metrics.csv` records within a numerical tolerance of $1 \times 10^{-4}$.
- Status: **PASS**.

### 3.2 Full Pipeline Reproducibility
The forecasting evaluation was re-executed from scratch into an isolated audit directory (`results/summaries/forecasting-audit/audit-20260910_184406/eval-20260910_184406`).
- Total predictions compared: 165 predictions.
- Prediction count match: 165 / 165 (100%).
- Numerical value match: 165 / 165 within $1 \times 10^{-5}$ tolerance.
- Exclusion count match: identical exclusions across all horizons.
- Model rankings match: identical ranking orders per horizon.
- Status: **PASS**.

---

## 4. Scenario Robustness and Comparative Performance

### 4.1 Comparison Against Persistence Baseline
Per-horizon performance was evaluated across all five scenarios on the validation split:

#### Horizon 1 ($h=1$, 15 seconds)
| Scenario | Persistence MAE | Best Candidate Model | Best Candidate MAE | Win/Tie/Loss vs Persistence |
| :--- | :--- | :--- | :--- | :--- |
| `steady` | 3.52 | `moving_average (w=3)` | 3.52 | Tie |
| `ramp` | 4.88 | `ewma (alpha=0.8)` | 4.90 | Loss |
| `periodic` | 6.22 | `persistence` | 6.22 | Native |
| `sudden_increase` | 6.10 | `persistence` | 6.10 | Native |
| `drift` | 4.72 | `ewma (alpha=0.8)` | 4.75 | Loss |
| **Macro Average** | **5.0882** | `persistence` | **5.0882** | **Persistence Wins** |

*Finding:* For Horizon 1, no moving average, EWMA, or linear trend model outperformed naive persistence. Persistence achieved lowest error across all 5 scenarios with zero data exclusions.

#### Horizon 2 ($h=2$, 30 seconds)
| Scenario | Persistence MAE | Best Candidate Model | Best Candidate MAE | Win/Tie/Loss vs Persistence |
| :--- | :--- | :--- | :--- | :--- |
| `steady` | 9.48 | `moving_average (w=3)` | 9.55 | Loss |
| `ramp` | 9.75 | `linear_trend (w=8)` | 10.12 | Loss |
| `periodic` | 13.44 | `persistence` | 13.44 | Native |
| `sudden_increase` | 12.20 | `persistence` | 12.20 | Native |
| `drift` | 11.95 | `ewma (alpha=0.8)` | 12.02 | Loss |
| **Macro Average** | **11.3645** | `persistence` | **11.3645** | **Persistence Wins** |

*Finding:* For Horizon 2, persistence again proved superior across all five scenarios. Trend-aware models introduce phase lag and overshoot without providing accuracy benefit at 30 seconds.

#### Horizon 4 ($h=4$, 60 seconds)
| Scenario | Persistence MAE | Linear Trend (w=8) MAE | EWMA (alpha=0.8) MAE | MA (w=3) MAE | Best Model |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `steady` | **14.98** | 35.08 | 15.22 | 16.40 | Persistence |
| `ramp` | 27.50 | **11.20** | 28.10 | 31.50 | Linear Trend |
| `periodic` | **14.15** | 18.21 | 15.02 | 16.88 | Persistence |
| `sudden_increase` | 48.40 | **12.50** | 49.10 | 52.30 | Linear Trend |
| `drift` | 32.18 | **16.72** | 33.05 | 35.12 | Linear Trend |
| **Macro Average** | 27.4432 | **18.7430** | 28.0980 | 30.4400 | **Linear Trend** |

*Finding:* At Horizon 4, `linear_trend (window=8)` decisively beats persistence on scenarios with directional dynamics (`ramp`, `sudden_increase`, `drift`), dropping macro validation MAE from 27.44 to 18.74. However, it exhibits significant degradation on `steady` (MAE 35.08 vs 14.98) and requires 8 history steps.

---

## 5. Validation Sensitivity Analysis

Parameter exploration on the validation split evaluated stability across adjacent configurations:

| Model Family | Horizon | Candidate Parameters | Validation Macro MAE | Stability Assessment |
| :--- | :--- | :--- | :--- | :--- |
| `ewma` | 1 | $\alpha \in \{0.2, 0.5, 0.8\}$ | 0.8: 5.12, 0.5: 6.84, 0.2: 9.35 | Monotonic improvement towards $\alpha \to 1.0$ (reverting to persistence). |
| `moving_average` | 1 | $w \in \{3, 5, 8\}$ | $w=3$: 5.18, $w=5$: 7.22, $w=8$: 10.45 | Shorter windows strictly dominate; smoothing introduces lag. |
| `linear_trend` | 4 | $w \in \{3, 5, 8\}$ | $w=8$: 18.74, $w=5$: 21.30, $w=3$: 26.50 | Longer windows stabilize slope estimation in the presence of noise. |

---

## 6. Operational Suitability and Micro-benchmarking

Micro-benchmarking on Python 3.14 was conducted over 1,000 iterations per model:

| Model Family | Mean Inference Time | p95 Latency | Max Latency | Feasible in 15s Cycle | Memory Overhead |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `persistence` | 6.21 µs | 6.30 µs | 44.5 µs | **YES** (0.00004% cycle) | Negligible |
| `moving_average` | 8.90 µs | 9.90 µs | 31.0 µs | **YES** (0.00006% cycle) | Negligible |
| `ewma` | 8.09 µs | 10.37 µs | 28.2 µs | **YES** (0.00005% cycle) | Negligible |
| `linear_trend` | 17.42 µs | 26.50 µs | 119.9 µs | **YES** (0.00012% cycle) | Negligible |

### Edge-Case Testing
All candidate models were tested on synthetic edge cases:
- Zero demand series: Produced exact 0.0 outputs without NaN/division by zero.
- Constant demand: Produced constant outputs matching inputs.
- Abrupt steps: Remained finite without numerical instability.
- Negative outputs: Properly clipped to 0.0 adhering to documented non-negative policy.

---

## 7. Final Model Selections and Engineering Rationale

### 7.1 Selection Decision Table

| Horizon | Selected Model | Frozen Parameters | Validation Macro MAE | Selection Status | Runtime Fallback |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1 (15s)** | `persistence` | `{}` | 5.0882 | `persistence_retained` | Native |
| **2 (30s)** | `persistence` | `{}` | 11.3645 | `persistence_retained` | Native |
| **4 (60s)** | `linear_trend` | `{"window": 8}` | 18.7430 | `offline_selected` | **Persistence Required** |

### 7.2 Default Controller Horizon Selection
**Selected Default Horizon:** **Horizon 1 (15 seconds)** using **`persistence`**.

#### Engineering Rationale:
1. **Prometheus Scraping Cycle Alignment:** The Prometheus scrape interval in KubePredict is configured for 15 seconds. Choosing a 1-step (15s) horizon aligns the forecasting observation interval directly with the controller control loop without requiring multi-step extrapolation or temporal resampling.
2. **Universal Robustness:** Horizon 1 with persistence achieves the lowest absolute error across all five scenarios (macro MAE 5.0882), zero scenario exclusions, and requires only 1 historical observation.
3. **Absence of Empirical Startup Delay Data:** While a 60-second horizon (Horizon 4) offers theoretical lead time for pod scheduling, cold pod startup times have not yet been empirically measured in the live cluster. Using Horizon 1 provides a safe, deterministic, zero-phase-distortion starting point.

### 7.3 Documented Weaknesses of Horizon 4 Selection
The selected Horizon 4 model (`linear_trend`, $w=8$) is frozen with explicit documented caveats:
1. **Steady Scenario Vulnerability:** On constant-load (`steady`) traffic, linear trend fits spurious noise slopes, generating validation MAE of 35.08 (vs. 14.98 for persistence) and test MAE of 79.69.
2. **High Minimum History Requirement:** Requires at least 8 continuous historical samples (120 seconds). On short scenario runs or pod restarts, it has an exclusion rate of up to 33%.
3. **Mandatory Persistence Fallback:** In `forecasting/service.py`, any request with $< 8$ historical observations automatically falls back to persistence, setting `fallback_used = True` and recording warning telemetry.

---

## 8. Runtime Forecast Contract

The pure in-process service wrapper `ForecastingService` implements the runtime contract defined in `forecasting/base.py`:

```python
class ForecastInput(BaseModel):
    generated_at: datetime  # Timezone-aware UTC
    sampling_interval_seconds: float  # > 0.0 (e.g. 15.0)
    observations: list[float]  # Finite, non-negative
    observation_timestamps: list[datetime]  # Chronological, matching length
    requested_horizon_steps: int  # > 0 (1, 2, or 4)


class ForecastOutput(BaseModel):
    generated_at: datetime
    model_name: str
    model_version: str
    horizon_steps: int
    horizon_seconds: float
    predicted_values: list[float]
    predicted_request_rate_per_second: float  # Matches last element
    confidence_inputs: dict[str, Any]
    history_count: int
    fallback_used: bool
    warnings: list[str]
```

### Frozen Configuration File
The frozen model configuration is persisted in [`forecasting/config/final_forecaster.yaml`](file:///c:/Users/arnav/Desktop/WebDev/adaptive-predictive-k8s-autoscaler/forecasting/config/final_forecaster.yaml).
- `schema_version`: `"1.0.0"`
- `status`: `"offline_selected"`
- `selection_evaluation_id`: `"eval-20260910_180143"`
- `selection_audit_id`: `"audit-20260910_184406"`
- `input_dataset_index_hash`: `"7a5fd04383a1dacbfc6bd9f489b6eaf27f5d740cb15584308ad2b78b24ee3ab0"`

---

## 9. Next Phase Readiness & Architectural Handoff

Phase 8B concludes the offline forecasting evaluation and audit. The frozen configuration and `ForecastingService` provide a verified, reproducible, in-process forecasting component ready for future integration into:
- The custom predictive autoscaler controller loop (Phase 9A).
- The forecast safety guard and rate limiter (Phase 9B).
- Comparative live Kubernetes experiments (Phase 10).
