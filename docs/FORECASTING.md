# Phase 8A: Offline Forecasting Baselines and Chronological Evaluation

## 1. Overview & Purpose

Phase 8A implements a reproducible, model-independent offline forecasting pipeline designed to evaluate time-series forecasting baselines on the telemetry datasets captured during Phase 7B.

The goal of this phase is purely offline model evaluation: comparing four model families across short forecast horizons, implementing strict chronological rolling-origin evaluation without future data leakage, and selecting provisional candidate models for review in Phase 8B.

> [!IMPORTANT]
> **Strict Offline Classification**: This phase performs offline workload forecasting only.
> It does **not** evaluate or prove:
> - Improved Kubernetes autoscaling
> - Reduced request latency or higher throughput
> - Reduced SLO violations or improved headroom
> - Lower cluster CPU or memory utilization
> - Superiority over Kubernetes Horizontal Pod Autoscaler (HPA)
>
> Forecasting accuracy is a necessary prerequisite, but it does **not** equal autoscaling performance. All models evaluated in Phase 8A are strictly provisional candidates. No model influences Kubernetes replicas or resources.

---

## 2. Telemetry Target & Dataset Source

- **Target Variable**: `request_rate_per_second` (HTTP incoming requests per second derived from Prometheus `sum(rate(apa_http_requests_total[1m]))`).
- **Data Source**: Phase 7B raw telemetry datasets stored in `data/raw/scenarios/` under five reproducible traffic profiles:
  - `steady` (constant moderate user load)
  - `ramp` (step-wise user load increase)
  - `periodic` (oscillating wave of user demand)
  - `sudden_increase` (abrupt step-change in demand)
  - `drift` (extended duration and amplitude variation)
- **Sampling Interval**: Aligned 15.00 seconds matching the Prometheus scrape interval.
- **Forecast Horizons**:
  - Step 1: 15 seconds (1 step)
  - Step 2: 30 seconds (2 steps)
  - Step 4: 60 seconds (4 steps)

---

## 3. Model-Independent Forecasting Interface

All forecasting models implement the abstract base class [`BaseForecaster`](file:///forecasting/base.py):

```python
class BaseForecaster(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]: ...

    @property
    @abstractmethod
    def minimum_history_required(self) -> int: ...

    def fit(self, history: Sequence[float] | np.ndarray) -> "BaseForecaster": ...

    def predict(self, horizon_steps: int) -> list[float]: ...
```

### Interface Guarantees
1. **Input Validation**: Rejects empty sequences, non-finite values (NaN/Inf), and negative request rates. Requires 1-D chronological observations with length $\ge \text{minimum\_history\_required}$.
2. **Caller Immutability**: Historical arrays are defensively copied; caller data is never mutated.
3. **Deterministic Output**: Identical history and parameter inputs always generate identical forecast vectors.
4. **Non-Negative Prediction Policy**: Because request rate cannot physically be negative, point predictions below 0.0 are clipped to 0.0 (`np.clip(p, 0.0, None)`).
5. **No Network / Cluster Access**: Models operate strictly as local numerical transformations with zero Kubernetes or Prometheus network interaction.

---

## 4. Implemented Forecasting Models

Four distinct model families are implemented and evaluated:

### 1. Persistence Baseline (`PersistenceForecaster`)
- **Role**: Mandatory naive baseline against which all non-naive models are compared.
- **Formula**: $\hat{y}_{t+h} = y_t$ for all $h \in \{1, \dots, H\}$.
- **Parameters**: None (`{}`). Minimum history required: 1 sample.

### 2. Trailing Moving Average Baseline (`MovingAverageForecaster`)
- **Formula**: Trailing mean over the latest configured window $w$:
  $$\bar{y} = \frac{1}{w} \sum_{i=0}^{w-1} y_{t-i}$$
  Repeated flat across the forecast horizon: $\hat{y}_{t+h} = \bar{y}$.
- **Candidate Windows**: $w \in \{3, 5, 8\}$ samples. Minimum history required: $w$ samples. Trailing only (strictly no centered window, zero future leakage).

### 3. Exponentially Weighted Moving Average (`EWMAForecaster`)
- **Formula**: Recursive exponential smoothing with initial state $s_0 = y_0$:
  $$s_i = \alpha y_i + (1 - \alpha) s_{i-1} \quad \text{for } i = 1, \dots, k-1$$
  The final smoothed state $s_{k-1}$ is repeated across all horizon steps: $\hat{y}_{t+h} = s_{k-1}$.
- **Candidate Alphas**: $\alpha \in \{0.2, 0.4, 0.6, 0.8\}$. Minimum history required: 1 sample.

### 4. Lightweight Linear Trend Model (`LinearTrendForecaster`)
- **Formula**: Trailing ordinary least squares (OLS) linear regression over time index $x \in \{0, \dots, w-1\}$ on the latest $w$ samples:
  $$m = \frac{\sum (x_i - \bar{x})(y_i - \bar{y})}{\sum (x_i - \bar{x})^2}, \quad b = \bar{y} - m \bar{x}$$
  Extrapolated to future steps $h \in \{1, \dots, H\}$:
  $$\hat{y}_{t+h} = \max(0.0, m \cdot ((w - 1) + h) + b)$$
- **Candidate Windows**: $w \in \{3, 5, 8\}$ samples. Minimum history required: $w$ samples ($w \ge 2$).

---

## 5. Preprocessing & Chronological Splitting Policy

1. **Chronological Splitting**: Each scenario run is partitioned independently without shuffling:
   - **Training Partition**: First 60% of samples (minimum 6 samples required).
   - **Validation Partition**: Middle 20% of samples (used for parameter ranking and selection).
   - **Test Partition**: Final 20% of samples (held out until parameters are frozen; evaluated once).
2. **Missing-Value Policy**:
   - Raw data is never fabricated, interpolated, or forward-filled across scenario boundaries.
   - If a forecast origin has missing/null observations in its required historical window or target horizon, the origin is excluded.
   - Every excluded origin is recorded with explicit metadata (`scenario`, `run_id`, `origin_index`, `target_index`, `reason`) in `excluded_origins.csv`.

---

## 6. Rolling-Origin Evaluation Engine

Rolling-origin evaluation advances step-by-step through each partition:
1. At forecast origin $t$, only observations at or before $t$ are accessible to the model.
2. The model fits on historical observations and forecasts horizon steps $h \in \{1, 2, 4\}$.
3. Partition membership is determined strictly by the target index ($t + h$):
   - Validation evaluation requires target index $\in [\text{val\_start}, \text{val\_end})$. Zero observations from the test split can ever be observed during validation.
   - Test evaluation requires target index $\in [\text{test\_start}, \text{test\_end})$. Test evaluation runs strictly once on frozen parameters.
4. Step size advances by 1 (`rolling_step_size = 1`).

---

## 7. Forecast Metrics

| Metric | Formula | Description |
| :--- | :--- | :--- |
| **MAE** | $\frac{1}{n} \sum \|y_i - \hat{y}_i\|$ | Mean Absolute Error (Primary Selection Metric). |
| **RMSE** | $\sqrt{\frac{1}{n} \sum (y_i - \hat{y}_i)^2}$ | Root Mean Squared Error (penalizes large outlier errors). |
| **sMAPE** | $\frac{100\%}{n} \sum \frac{2 \|y_i - \hat{y}_i\|}{\|y_i\| + \|\hat{y}_i\|}$ | Symmetric Mean Absolute Percentage Error in $[0, 200]\%$. Zero/zero pairs evaluate to $0.0\%$. |
| **Bias** | $\frac{1}{n} \sum (\hat{y}_i - y_i)$ | Mean error. Positive indicates over-prediction; negative indicates under-prediction. |

Metrics are aggregated at both macro (unweighted mean across scenarios) and weighted (by sample count) levels to prevent one scenario from masking poor performance on others.

---

## 8. Parameter Selection & Baseline Comparison Rule

1. **Parameter Ranking**: Evaluated exclusively on the validation split using macro MAE as the primary metric, with RMSE and sMAPE as secondary tie-breakers.
2. **Freezing Rule**: The top-performing parameter set for each model family is frozen.
3. **Baseline Comparison Rule**: A non-naive model qualifies as a provisional candidate for a horizon only if:
   - It possesses sufficient valid predictions.
   - Its validation macro MAE is no worse than persistence: $\text{MAE}_{\text{candidate}} \le \text{MAE}_{\text{persistence}} + 0.0001$.
   - Its output is finite and stable across required scenarios.
4. **Persistence Fallback**: If no non-naive model outperforms or matches persistence, **the persistence baseline is retained as the provisional candidate**.

---

## 9. Output Directory Structure

Generated numerical evidence is stored outside Git:
```
data/processed/forecasting/<evaluation-id>/
    evaluation_config.yaml
    input_dataset_index.json
    data_quality_report.json
    validation_predictions.csv
    test_predictions.csv
    validation_metrics.csv
    test_metrics.csv
    validation_ranking.csv
    provisional_selection.json
    excluded_origins.csv
    run_metadata.json
    execution.log

results/summaries/forecasting/<evaluation-id>/
    evaluation_summary.json
    evaluation_summary.md
```

---

## 10. CLI & PowerShell Operations

```powershell
# Validate forecasting configuration and Phase 7B datasets offline (0 predictions, 0 mutations)
.\scripts\forecasting\validate-data.ps1

# Run chronological evaluation across all Phase 7B scenarios and baselines
.\scripts\forecasting\evaluate-baselines.ps1

# Inspect results and provisional candidate selection
.\scripts\forecasting\inspect-results.ps1 -EvaluationDir data/processed/forecasting/<eval-id>
```

---

## 11. Phase 8B Audit and Frozen Model Selection

Phase 8B executed a comprehensive independent audit of the Phase 8A evaluation pipeline and baseline outputs, verifying:
- **Input Data Immutability:** SHA-256 verification of raw datasets and configuration.
- **Chronological Split Integrity:** Monotonic partitions without gaps or overlaps.
- **Leakage Traps:** 6 explicit behavioral tests passing (future perturbation, split perturbation, target column, timestamp offset, scenario boundary, preprocessing fit).
- **Independent Metric Reproduction:** Exact match for MAE, RMSE, sMAPE, and bias within $1 \times 10^{-4}$.
- **Scenario Robustness:** Persistence retained for Horizons 1 and 2; Linear Trend ($w=8$) selected for Horizon 4 with documented weaknesses and mandatory persistence fallback.
- **Frozen Configuration:** Stored in [`forecasting/config/final_forecaster.yaml`](file:///forecasting/config/final_forecaster.yaml) with status `offline_selected`.
- **Pure In-Process Service:** Runtime contract implemented via `ForecastingService` in [`forecasting/service.py`](file:///forecasting/service.py).
- **Full Report:** See [`docs/FORECAST_SELECTION.md`](file:///docs/FORECAST_SELECTION.md).

---

## 12. Scope Limitations & Research Constraints

- **No Controller Code**: This phase contains no Kubernetes client, no Prometheus polling daemon, no replica sizing logic, no forecast guard, and no reactive fallback.
- **No Production Claims**: Status is strictly `offline_selected` — NOT `production_ready`. Models are evaluated purely offline on captured telemetry datasets.
- **Independent Execution**: Forecasting scripts operate entirely offline from raw CSV files without requiring Kubernetes, Docker, or external network connectivity.
