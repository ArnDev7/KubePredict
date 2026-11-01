# Dry-Run Arbitration, Rapid-Change Detection, and Reactive Fallback Policy (Phase 10B)

## 1. Policy Purpose

Phase 10B completes the dry-run policy arbitration layer of **KubePredict: Adaptive Predictive Autoscaling for Cloud-Native Applications Using Kubernetes**.

The arbitration engine coordinates multiple inputs—live Prometheus request-rate telemetry, frozen point forecasts, trust guard validation decisions, rapid-change detection, and read-only CPU utilization telemetry—into a single deterministic scaling decision. All decisions enforce safe replica bounds, scale-up and scale-down step limits, cooldown dampening, and downscale stabilization.

```
Live Telemetry (Prometheus)
      ↓
Frozen Forecast (ForecastingService)
      ↓
Forecast Trust Guard (evaluate_forecast_trust)
      ↓
Rapid-Change Detector (detect_rapid_change)
      ↓
Predictive Capacity Recommendation (if trusted)
      OR
Reactive Fallback Recommendation (if forecast untrusted & CPU available)
      ↓
Policy Arbitration (arbitrate_policy_decision)
      ↓
Replica Bounds [min_replicas, max_replicas]
      ↓
Maximum Scale Steps [+max_scale_up_step, -max_scale_down_step]
      ↓
Cooldown Dampening (CooldownManager)
      ↓
Downscale Stabilization (DownscaleStabilizer)
      ↓
Final Dry-Run Decision Record
      ↓
action_executed = False (Strictly Zero Kubernetes Mutation)
```

Phase 10B operates strictly in **dry-run mode**: `action_executed` is unconditionally `False`, no Kubernetes scale subresource APIs are invoked, no mutation kubectl commands exist, and no controller RBAC or Deployment manifests are installed.

---

## 2. Rapid-Change Detector

### Mathematical Formulation
The rapid-change detector (`controller/rapid_change.py`) monitors recent request-rate observations over a small sliding window ($W$ samples) to detect sudden traffic surges that might outpace slower forecast trends or occur during residual collection periods.

For an ordered sequence of observed request rates $r_1, r_2, \dots, r_W$:
- **Baseline Rate**: Mean of historical samples prior to the latest observation:
  $$\text{baseline\_rate} = \frac{1}{W - 1} \sum_{i=1}^{W-1} r_i$$
- **Recent Rate**: The most recent sample in the window:
  $$\text{recent\_rate} = r_W$$
- **Absolute Increase**:
  $$\Delta r = \text{recent\_rate} - \text{baseline\_rate}$$
- **Growth Ratio**:
  $$\rho = \frac{\text{recent\_rate}}{\max(\text{baseline\_rate}, \epsilon)}$$
  where $\epsilon = 10^{-6}\,\text{req/s}$.

### Detection Conditions
Rapid change is detected ($\text{detected} = \text{True}$) if and only if **all** of the following conditions hold:
1. **Sample Count**: Number of valid samples $\ge W$ ($\ge 2$).
2. **Freshness**: Latest observation is within the configured maximum telemetry age.
3. **Monotonicity**: Observation timestamps are strictly monotonically increasing.
4. **Absolute Threshold**: $\Delta r \ge \text{rapid\_change\_absolute\_rate}$ (configured default: $5.0\,\text{req/s}$).
5. **Ratio Threshold**: $\rho \ge \text{rapid\_change\_growth\_ratio}$ (configured default: $2.0\times$).

### Safe Near-Zero Handling
When baseline traffic is near zero ($\text{baseline\_rate} < \epsilon$):
- Division by zero is avoided via $\max(\text{baseline\_rate}, \epsilon)$.
- A ratio surge alone on near-zero noise (e.g., $0.001 \to 0.004\,\text{req/s}$, a $4.0\times$ increase) **cannot** trigger detection because the absolute threshold ($\Delta r \ge 5.0\,\text{req/s}$) is strictly enforced.
- No adaptive learning or complex statistical drift detectors (ADWIN, Page-Hinkley, CUSUM) are used; the detector is fully deterministic and auditable.

---

## 3. Reactive Fallback

### Formulation
When point forecasts cannot be trusted (e.g., during warm-up, telemetry anomalies, or threshold violations), KubePredict falls back to a transparent, dry-run proportional CPU reactive recommendation (`controller/reactive_fallback.py`):

$$\text{raw\_replicas} = \left\lceil \text{current\_replicas} \times \frac{\text{current\_cpu\_utilization\_ratio}}{\text{reactive\_cpu\_target\_ratio}} \right\rceil$$

Where:
- $\text{current\_replicas} \ge 1$
- $\text{current\_cpu\_utilization\_ratio} = \frac{\text{total pod CPU cores consumed}}{\text{total pod CPU cores requested}}$
- $\text{reactive\_cpu\_target\_ratio} = 0.70$ ($70\%$ CPU utilization target)

### Safeguards and Distinction from Phase 5 HPA
- If CPU telemetry is missing, stale, negative, or non-finite, reactive fallback produces **no recommendation** (`None`). Missing CPU data is never silently treated as zero.
- This fallback provides an in-memory heuristic for dry-run arbitration. It is **not** a replacement for the Phase 5 Kubernetes HorizontalPodAutoscaler (HPA), which remains inactive during Phase 10B.

---

## 4. Read-Only CPU Telemetry Collector

CPU metrics are gathered via a strictly read-only collector (`controller/cpu_telemetry.py`):
- Executes `kubectl top pod -n kubepredict-system --context kind-kubepredict-dev` via `subprocess.run(shell=False)` with a bounded 10-second timeout.
- Filters pods matching prefix `kubepredict-workload-api`.
- Parses CPU usage in millicores (`m`) or whole cores, aggregating across pods.
- Computes utilization ratio against configured pod resource requests ($100\,\text{m}$ CPU request per pod).
- Dependency injection via `CpuTelemetryProvider` allows pure unit tests without cluster access.

---

## 5. Deterministic Arbitration Order

The arbitration engine (`controller/arbitration.py`) resolves policies in a deterministic priority order:

1. **Telemetry Freshness & Availability**: If request-rate telemetry is unavailable, stale, or contains non-finite values $\to$ `PolicyMode.NO_RECOMMENDATION` (`telemetry_unavailable` or `telemetry_stale`).
2. **Forecast Availability**: If forecasting service produces no forecast $\to$ evaluate reactive fallback. If fallback unavailable $\to$ `PolicyMode.NO_RECOMMENDATION` (`forecast_unavailable`).
3. **Forecast Trust Evaluation**: Evaluate `evaluate_forecast_trust` (Phase 10A).
4. **Rapid-Change Evaluation**: Run `detect_rapid_change`.
5. **Read-Only CPU Telemetry Retrieval**: If rapid change is detected or forecast is untrusted, query CPU telemetry.
6. **Policy Branch Selection**:
   - **`rapid_change_override`**: Selected when rapid change is detected **and** a valid reactive recommendation exists. This ensures an emergent traffic spike overrides a lagging forecast.
   - **`predictive`**: Selected when the forecast is **trusted**, rapid change is **not** detected, and a valid predictive capacity recommendation exists.
   - **`reactive_fallback`**: Selected when the forecast is **untrusted** and a valid reactive fallback recommendation exists.
   - **`no_recommendation`**: Selected when neither valid predictive nor valid reactive recommendations exist. Current replicas are **never** silently fabricated as a recommendation.

---

## 6. Policy Constraints & Dampening

Every selected raw recommendation is filtered through sequential constraint stages, retaining audit visibility at each step:

1. **Replica Bounds**:
   $$\text{bounded\_rec} = \max(\text{minimum\_replicas}, \min(\text{maximum\_replicas}, \text{unconstrained\_rec}))$$
   Default bounds: $[1, 10]$ replicas.
2. **Maximum Scale Steps**:
   - Scale-up: $\min(\text{current\_replicas} + \text{max\_scale\_up\_step}, \text{bounded\_rec})$ (default step: $+3$)
   - Scale-down: $\max(\text{current\_replicas} - \text{max\_scale\_down\_step}, \text{bounded\_rec})$ (default step: $-1$)
3. **Cooldown Dampening** (`controller/cooldown.py`):
   - Following any scaling recommendation that differs from current replicas, cooldown is active for `cooldown_seconds` (default: $60.0\,\text{s}$).
   - While cooldown is active, the final recommendation is held at `current_replicas` with reason `cooldown_active`. The unconstrained recommendation is preserved in the evidence record.
4. **Downscale Stabilization** (`controller/stabilization.py`):
   - For scale-down decisions, recent recommendations within the stabilization window (`downscale_stabilization_seconds`, default: $90.0\,\text{s}$) are inspected.
   - The stabilizer chooses the safest (highest) replica recommendation observed across the window:
     $$\text{stabilized\_rec} = \max(\{r \mid (t, r) \in \text{window}\})$$
   - Scale-up decisions are never delayed by stabilization.

---

## 7. Arbitration Decision Contract

Every cycle produces a typed `ArbitrationDecisionRecord` (`controller/arbitration_decision.py`) with strict invariants:

| Field | Type | Description |
|---|---|---|
| `timestamp` | `datetime` | Timezone-aware UTC evaluation timestamp |
| `cycle_number` | `int` | Sequential 1-based cycle counter |
| `policy_mode` | `PolicyMode` | `predictive`, `reactive_fallback`, `rapid_change_override`, or `no_recommendation` |
| `primary_reason` | `DecisionReason` | Traceable reason enum (no arbitrary strings) |
| `telemetry_status` | `str` | Status of request-rate telemetry (`valid`, `stale`, `unavailable`) |
| `forecast` | `ForecastResult | None` | Generated point forecast record |
| `trust_decision` | `ForecastTrustDecision | None` | Phase 10A trust outcome |
| `rapid_change_result` | `RapidChangeResult | None` | Surge detector diagnostic record |
| `reactive_input` | `ReactiveTelemetryInput | None` | Input pod count and CPU utilization ratio |
| `predictive_recommendation` | `int | None` | Raw recommendation from capacity model |
| `reactive_recommendation` | `int | None` | Raw recommendation from reactive fallback |
| `selected_unconstrained_recommendation` | `int | None` | Mode-selected raw recommendation |
| `bounded_recommendation` | `int | None` | Recommendation after $[1, 10]$ clipping |
| `step_limited_recommendation` | `int | None` | Recommendation after $[+3, -1]$ limits |
| `cooldown_blocked` | `bool` | True if cooldown held recommendation at current |
| `stabilized_recommendation` | `int | None` | Recommendation after downscale window smoothing |
| `final_recommendation` | `int | None` | Final dry-run replica decision (`None` if no recommendation) |
| `direction` | `ScalingDirection` | `scale_up`, `scale_down`, or `no_change` |
| `applied_constraints` | `list[str]` | List of constraints modifying recommendation |
| `action_executed` | `bool` | **Always `False`** |
| `warnings` | `list[str]` | Non-fatal diagnostic warnings |
| `details` | `dict[str, Any]` | Detailed calculation metadata |

---

## 8. Fixed-Cardinality Prometheus Metrics

The local metrics endpoint (`http://127.0.0.1:9101/metrics`) exposes:

| Metric Name | Type | Labels | Description |
|---|---|---|---|
| `kubepredict_policy_decisions_total` | Counter | `mode`, `reason`, `direction` | Decision count per mode, reason, and direction |
| `kubepredict_policy_mode` | Gauge | `mode` | 1 if active in latest cycle, 0 otherwise |
| `kubepredict_rapid_change_detected` | Gauge | None | 1 if rapid change detected, 0 otherwise |
| `kubepredict_rapid_change_growth_ratio` | Gauge | None | Recent / baseline growth ratio |
| `kubepredict_reactive_cpu_utilization_ratio` | Gauge | None | Current pod CPU utilization ratio |
| `kubepredict_reactive_recommendation` | Gauge | None | Reactive fallback replica value |
| `kubepredict_predictive_recommendation` | Gauge | None | Predictive capacity replica value |
| `kubepredict_final_dry_run_recommendation` | Gauge | None | Final arbitrated dry-run replica decision |
| `kubepredict_cooldown_active` | Gauge | None | 1 if cooldown blocked a change, 0 otherwise |
| `kubepredict_downscale_stabilized` | Gauge | None | 1 if downscale was dampened, 0 otherwise |
| `kubepredict_policy_no_recommendation_total` | Counter | `reason` | Count of no-recommendation cycles by reason |

No unbounded labels (timestamps, forecast IDs, pod names) are permitted.

---

## 9. Output Evidence Schema

Artifacts are persisted in `experiments/output/controller-arbitration/<run-id>/`:
- `metadata.json`: Run parameters, timing, cluster context, and cycle summaries.
- `cycles.jsonl`: Complete serialized `ArbitrationCycleResult` per cycle.
- `arbitration_decisions.csv`: High-level arbitration trajectory.
- `forecasts.csv`: Generated point forecasts.
- `trust_decisions.csv`: Trust guard decisions and residual metrics.
- `rapid_change_decisions.csv`: Rapid-change detector samples, ratios, and outcomes.
- `reactive_recommendations.csv`: Reactive fallback calculations and CPU ratios.
- `matched_residuals.csv`: Matched forecast residuals over the evaluation window.
- `skipped_cycles.csv`: Records of skipped cycles (if any).
- `policy_configuration.yaml`: Copy of the active policy configuration.
- `validation_report.json`: Pre-execution validation check results.
- `execution.log`: Full controller runtime log.

---

## 10. CLI and PowerShell Automation

### CLI Subcommands
- `python -m controller.cli validate-arbitration`: Validates configuration schemas and deterministic policy paths.
- `python -m controller.cli arbitration-dry-run`: Runs bounded arbitrated dry-run controller with Prometheus metrics server.

### PowerShell Automation Scripts
- `scripts/controller/validate-arbitration.ps1`: Zero-network, zero-cluster validation of all 5 configuration files and deterministic path tests.
- `scripts/controller/run-arbitration-dry-run.ps1`: Verifies cluster prerequisites, starts temporary Prometheus port-forward, runs bounded dry-run cycles, cleans up processes in `finally`.
- `scripts/controller/inspect-arbitration-run.ps1`: Audits run artifact bundle, verifies `action_executed=False`, and reports mode/reason distributions.

---

## 11. Strict Safety Boundaries and Non-Mutation

1. **Zero Kubernetes Mutation**:
   - `action_executed` is strictly hard-coded to `False` in code and schemas.
   - No Kubernetes client mutation APIs (`create`, `update`, `patch`, `delete`, `scale`) are imported or executed.
   - Workload remains fixed at 2 replicas.
2. **No Live Infrastructure Mutation**:
   - No controller ServiceAccount, Role, or RoleBinding exists.
   - No controller Deployment or container image is created.
   - HorizontalPodAutoscaler (HPA) remains uninstalled and inactive.
3. **No Drift / Reinforcement Learning**:
   - Complex statistical drift detectors (ADWIN, Page-Hinkley, CUSUM) are not implemented.
   - Thresholds are deterministic and configured via YAML.
