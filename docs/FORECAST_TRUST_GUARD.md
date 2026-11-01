# Forecast Trust Guard (Phase 10A)

## 1. Purpose

The Forecast Trust Guard is an explicit, auditable reliability barrier positioned between forecast generation and autoscaling recommendation policy in KubePredict. It decides whether the current point forecast is eligible to influence a future scaling policy by evaluating telemetry availability, telemetry freshness, forecast numerical validity, and recent forecast error over a sliding window of matched residuals.

```
Live Telemetry
      ↓
Frozen Forecaster (ForecastingService)
      ↓
ForecastOutput / PendingForecast
      ↓
Forecast Trust Guard (evaluate_forecast_trust)
      ↓
ForecastTrustDecision
      ↓
If Trusted:
    Dry-run capacity recommendation marked recommendation_eligible=True
If Untrusted:
    Recommendation eligibility blocked (recommendation_eligible=False)
      ↓
action_executed remains strictly False (Zero Kubernetes mutations)
```

## 2. Exact Phase Boundary

Phase 10A implements strictly:
- Trust guard configuration schema (`TrustGuardConfig`)
- Sliding residual window construction (`build_residual_window`)
- Window error metric calculations (`calculate_trust_metrics`: MAE, RMSE, sMAPE, signed bias)
- Deterministic trust evaluation order and outcome enumeration (`TrustOutcome`)
- Typed trust decision contract (`ForecastTrustDecision`)
- Guarded dry-run cycle execution (`execute_guarded_dry_run_cycle`)
- Fixed-cardinality Prometheus operational metrics (`GuardedDryRunMetrics`)
- Static source write-capability audit
- PowerShell helpers and CLI integration
- Unit and integration tests

Phase 10A strictly excludes:
- Workload drift detection (Page-Hinkley, ADWIN, CUSUM)
- Burst detection (request-rate slope override, queue-growth override)
- Reactive fallback (an untrusted forecast blocks recommendation eligibility; it does NOT invoke reactive fallback)
- Cooldown, downscale stabilization, or hysteresis
- Predictive vs. reactive arbitration
- Kubernetes client imports or API write calls
- Controller ServiceAccount, Role, RoleBinding, or Deployment
- Live scaling actuation or HPA activation
- Controller comparison experiments or latency claims

## 3. Trust Outcomes

A fixed enumeration (`TrustOutcome`) defines all deterministic guard outcomes:

| Outcome | Trusted? | Semantic Meaning |
|---|---|---|
| `trusted` | `True` | All gates passed; forecast error within configured thresholds. |
| `guard_disabled` | `False` | Trust guard is disabled by configuration. |
| `telemetry_unavailable` | `False` | Telemetry result is missing, unavailable, or malformed. |
| `telemetry_stale` | `False` | Telemetry exceeds freshness tolerance. |
| `insufficient_history` | `False` | Telemetry observations below required minimum. |
| `forecast_unavailable` | `False` | No forecast output was produced. |
| `forecast_invalid` | `False` | Predicted rate is negative, NaN, or infinite. |
| `insufficient_residual_history` | `False` | Fewer than `minimum_matched_residuals` eligible in window. |
| `residual_history_stale` | `False` | Most recent matched residual exceeds `residual_max_age_seconds`. |
| `absolute_error_too_high` | `False` | Recent MAE exceeds `absolute_error_threshold`. |
| `relative_error_too_high` | `False` | Recent sMAPE exceeds `relative_error_threshold`. |
| `error_threshold_exceeded` | `False` | Both absolute and relative error thresholds exceeded. |

No arbitrary string reasons or exception messages are permitted in outcome labels.

## 4. Configuration Specification

Configuration file: `controller/config/trust_guard.yaml`
Config model: `controller/trust_config.py` (`TrustGuardConfig`)

| Field | Type | Default | Validation Constraint | Description |
|---|---|---|---|---|
| `schema_version` | string | `"1.0.0"` | Non-empty | Schema version |
| `status` | string | `"provisional_validated"` | `provisional_validated` or `provisional_configured` | Validation state (`production_ready` forbidden) |
| `enabled` | boolean | `true` | Boolean | Whether guard evaluation is active |
| `evidence_window_seconds` | float | `180.0` | `> 0.0` | Sliding window for residual consideration |
| `minimum_matched_residuals` | integer | `3` | `> 0` | Minimum matched residuals required for trust |
| `residual_max_age_seconds` | float | `120.0` | `> 0.0` | Maximum age of the latest matched residual |
| `absolute_error_threshold` | float | `10.0` | `>= 0.0` | Maximum allowable MAE (req/s) |
| `relative_error_threshold` | float | `50.0` | `>= 0.0` | Maximum allowable sMAPE (%) |
| `primary_error_metric` | string | `"mae"` | `mae`, `rmse`, `smape` | Primary metric for absolute error evaluation |
| `require_fresh_telemetry` | boolean | `true` | Boolean | Enforce telemetry freshness gate |
| `require_valid_forecast` | boolean | `true` | Boolean | Enforce forecast numerical validity gate |
| `minimum_history_observations` | integer | `1` | `> 0` | Minimum observation history required |
| `threshold_source` | string | `"..."` | Non-empty | Provenance of derived thresholds |
| `threshold_evidence_reference` | string | `"..."` | Relative path(s) | Relative path to evidence artifacts |

## 5. Threshold Derivation & Provenance

Thresholds are derived conservatively from two empirical sources:
1. **Offline Validation Macro Metrics (Phase 8B)**:
   - File: `forecasting/config/final_forecaster.yaml#L70-L79`
   - Evaluation ID: `eval-20260910_180143`
   - Model: Frozen Persistence baseline at horizon 1 (15s)
   - Macro MAE: 5.0882 req/s across 5 required scenarios (`steady`, `ramp`, `periodic`, `sudden_increase`, `drift`)
   - Macro sMAPE: 6.858%
2. **Live Baseline Observer & Dry-Run Residuals (Phase 9A/9B)**:
   - File: `experiments/output/controller-dry-run/dry-run-20261027_105511/matched_residuals.csv`
   - Samples: 11 live matched residuals under baseline background cluster traffic (~0.73 req/s)
   - Measured MAE: 0.0445 req/s
   - Measured sMAPE: ~6.0%

**Derived Threshold Values**:
- `absolute_error_threshold`: 10.0 req/s (~2.0× Phase 8B validation macro MAE, accommodating scenario variance while blocking gross prediction divergence).
- `relative_error_threshold`: 50.0% sMAPE (substantially above baseline 6.0% and validation 6.86%, accommodating low-traffic noise while detecting severe proportional error).
- `status`: `provisional_validated` (traceable to historical validation and live residuals; NOT claimed as production-ready).

## 6. Error Metric Formulas

All metrics are computed over eligible matched residuals in the recent evidence window:

### Mean Absolute Error (MAE)
$$\text{MAE} = \frac{1}{n} \sum_{i=1}^n |\text{actual}_i - \text{predicted}_i|$$

### Root Mean Squared Error (RMSE)
$$\text{RMSE} = \sqrt{\frac{1}{n} \sum_{i=1}^n (\text{actual}_i - \text{predicted}_i)^2}$$

### Symmetric Mean Absolute Percentage Error (sMAPE)
$$\text{sMAPE} = \frac{100\%}{n} \sum_{i=1}^n \frac{2 \cdot |\text{actual}_i - \text{predicted}_i|}{|\text{actual}_i| + |\text{predicted}_i|}$$

### Signed Bias
$$\text{Bias} = \frac{1}{n} \sum_{i=1}^n (\text{predicted}_i - \text{actual}_i)$$

### Zero-Traffic Handling Policy
When both $\text{actual}_i = 0.0$ and $\text{predicted}_i = 0.0$, the sMAPE term is defined strictly as `0.0`, preventing division by zero. Zero traffic with zero forecast error yields `MAE = 0.0` and `sMAPE = 0.0%`, which is eligible for trust when residual count and freshness requirements pass.

## 7. Residual Window Construction

The module `controller/residual_window.py` filters matched residuals:
- **Boundary**: Records must fall in $[\text{eval\_time} - \text{evidence\_window\_seconds}, \text{eval\_time}]$.
- **No Future Data**: Records with $\text{target\_timestamp} > \text{eval\_time}$ are strictly excluded.
- **Unmatched Excluded**: Records with status `pending` or `expired` without actual observations are excluded from error calculations.
- **Deterministic Ordering**: Eligible records are sorted deterministically by $(\text{target\_timestamp}, \text{forecast\_id})$.
- **No Mutation**: The caller's store records are never mutated.

## 8. Deterministic Evaluation Order

`controller/trust_guard.py` evaluates gates in strict short-circuit order:

1. `guard_enabled`: If disabled, return `guard_disabled`.
2. `telemetry_available`: If telemetry is None/unavailable/malformed, return `telemetry_unavailable`.
3. `telemetry_fresh`: If telemetry is stale, return `telemetry_stale`.
4. `history_sufficient`: If observation count < minimum, return `insufficient_history`.
5. `forecast_available`: If forecast is None, return `forecast_unavailable`.
6. `forecast_valid`: If predicted rate < 0 or non-finite, return `forecast_invalid`.
7. `build_residual_window`: Extract eligible matched records.
8. `residual_count_sufficient`: If eligible count < minimum, return `insufficient_residual_history`.
9. `residual_fresh`: If latest residual age > max age, return `residual_history_stale`.
10. `calculate_trust_metrics`: Compute MAE, RMSE, sMAPE, bias.
11. `evaluate_thresholds`:
    - If MAE > threshold AND sMAPE > threshold: return `error_threshold_exceeded`.
    - If MAE > threshold: return `absolute_error_too_high`.
    - If sMAPE > threshold: return `relative_error_too_high`.
12. All gates pass: return `trusted`.

## 9. Guarded Dry-Run Decision Flow

When integrated into the dry-run controller loop (`controller/guarded_dry_run.py`):
1. Telemetry is queried from Prometheus.
2. Target-reached pending forecasts are matched and errors recorded.
3. Point forecast is generated via frozen `ForecastingService`.
4. Trust Guard evaluates the forecast.
5. If `trusted`:
   - Deployment status is read (read-only).
   - Capacity model computes recommended replicas.
   - Record `recommendation_eligible = True`.
   - `outcome = "guarded_dry_run_trusted_eligible"`.
6. If `untrusted`:
   - Recommendation eligibility is blocked (`recommendation_eligible = False`).
   - No capacity recommendation is generated for policy use.
   - No reactive fallback is triggered.
   - `outcome = "guarded_dry_run_untrusted_blocked"`.
7. `action_executed` is strictly `False`. Zero Kubernetes mutations occur.

## 10. Operational Metrics

Exposed at `http://127.0.0.1:9101/metrics` via `GuardedDryRunMetrics`:

| Metric Name | Type | Labels | Description |
|---|---|---|---|
| `kubepredict_guard_evaluations_total` | Counter | `outcome`, `trusted` | Total trust evaluations by outcome |
| `kubepredict_guard_trusted_state` | Gauge | None | 1.0 if trusted, 0.0 if untrusted |
| `kubepredict_guard_matched_residuals` | Gauge | None | Count of eligible matched residuals in window |
| `kubepredict_guard_recent_mae` | Gauge | None | Recent MAE (req/s) |
| `kubepredict_guard_recent_rmse` | Gauge | None | Recent RMSE (req/s) |
| `kubepredict_guard_recent_smape` | Gauge | None | Recent sMAPE (%) |
| `kubepredict_guard_recent_bias` | Gauge | None | Recent signed bias (req/s) |
| `kubepredict_guard_residual_age_seconds` | Gauge | None | Age of latest residual in seconds |
| `kubepredict_guard_recommendation_eligible` | Gauge | None | 1.0 if eligible, 0.0 if blocked |

Label values use fixed enumerations only. No high-cardinality IDs, timestamps, or floating-point values are used as labels.

## 11. Artifact Output Structure

Stored in `experiments/output/controller-trust-guard/<run-id>/`:
- `metadata.json`: Run provenance, cycle counts, trusted/untrusted breakdown, hash references.
- `cycles.jsonl`: Complete structured record per cycle.
- `forecasts.csv`: Generated forecasts and pending/matched status.
- `trust_decisions.csv`: Full audit trail of each trust evaluation (includes `action_executed=False`).
- `matched_residuals.csv`: Matched predictions, actuals, and residuals.
- `guarded_recommendations.csv`: Replica recommendations and eligibility flags.
- `skipped_cycles.csv`: Detail on blocked or skipped cycles.
- `trust_guard_configuration.yaml`: Copy of active configuration.
- `validation_report.json`: Verification report asserting all safety contracts.
- `execution.log`: Execution log output.

## 12. CLI & PowerShell Commands

```powershell
# Validate trust guard configuration and run deterministic tests
powershell -ExecutionPolicy Bypass -File scripts/controller/validate-trust-guard.ps1

# Run bounded guarded dry-run controller loop (16 cycles)
powershell -ExecutionPolicy Bypass -File scripts/controller/run-guarded-dry-run.ps1 -MaximumCycles 16

# Inspect and audit a completed guarded run
powershell -ExecutionPolicy Bypass -File scripts/controller/inspect-trust-guard-run.ps1 -RunDir experiments/output/controller-trust-guard/<run-id>
```

## 13. Safety Audit & Non-Mutation

Static AST audit (`python -m controller.cli audit-source`) verifies across all controller Python and script files:
- Zero imports of `kubernetes`, `kubernetes.client`, or `kubernetes.config`.
- Zero write methods (`patch_namespaced_deployment_scale`, `replace_namespaced_deployment_scale`, etc.).
- Zero write-capable `kubectl` verbs (`apply`, `scale`, `patch`, `delete`, `replace`, `create`, `edit`).
- Zero `shell=True` subprocess calls.
- Zero live scaling CLI options (`--live`, `--apply`, `--scale`).
- Zero premature feature calls (`reactive_fallback`, `burst_override`, `workload_drift`).
