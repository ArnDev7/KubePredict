"""Pure deterministic dry-run policy arbitration engine for KubePredict (Phase 10B).

Coordinates telemetry validation, forecast trust guard, rapid-change detection,
reactive CPU fallback, replica bounds, step limits, downscale stabilization, and cooldown.
Zero Kubernetes API write access, zero live scaling actuation, and strict action_executed=False.
"""

from datetime import datetime
from typing import Any

from controller.arbitration_decision import (
    ArbitrationDecisionRecord,
    ReactiveTelemetryInput,
)
from controller.capacity import CapacityResult
from controller.cooldown import CooldownManager
from controller.cpu_telemetry import CpuTelemetryResult
from controller.policy_config import PolicyConfig
from controller.rapid_change import RapidChangeResult
from controller.reactive_fallback import calculate_reactive_fallback
from controller.stabilization import DownscaleStabilizer
from controller.telemetry import TelemetryResult
from shared.enums import DecisionReason, PolicyMode, ScalingDirection
from shared.models import ForecastResult, ForecastTrustDecision
from shared.validation import require_timezone_aware


def arbitrate_policy_decision(
    timestamp: datetime,
    cycle_number: int,
    current_replicas: int,
    telemetry: TelemetryResult | None,
    forecast: ForecastResult | None,
    trust_decision: ForecastTrustDecision | None,
    rapid_change_result: RapidChangeResult | None,
    predictive_capacity_result: CapacityResult | None,
    cpu_telemetry: CpuTelemetryResult | None,
    config: PolicyConfig,
    cooldown_manager: CooldownManager | None = None,
    stabilizer: DownscaleStabilizer | None = None,
    extra_details: dict[str, Any] | None = None,
) -> ArbitrationDecisionRecord:
    """Execute deterministic policy arbitration and apply dry-run safety constraints.

    Parameters
    ----------
    timestamp : datetime
        Timestamp of arbitration cycle.
    cycle_number : int
        Current sequential cycle number.
    current_replicas : int
        Current actual replica count (> 0).
    telemetry : TelemetryResult | None
        Recent Prometheus telemetry result.
    forecast : ForecastResult | None
        Recent point forecast result.
    trust_decision : ForecastTrustDecision | None
        Phase 10A trust decision.
    rapid_change_result : RapidChangeResult | None
        Rapid-change detector result.
    predictive_capacity_result : CapacityResult | None
        Predictive capacity model result (if generated).
    cpu_telemetry : CpuTelemetryResult | None
        Read-only pod CPU utilization snapshot.
    config : PolicyConfig
        Validated policy configuration.
    cooldown_manager : CooldownManager | None
        Optional cooldown state tracker.
    stabilizer : DownscaleStabilizer | None
        Optional downscale stabilization window tracker.
    extra_details : dict[str, Any] | None
        Optional operational debug metadata.

    Returns
    -------
    ArbitrationDecisionRecord
        Auditable, strictly read-only decision record.
    """
    ts_utc = require_timezone_aware(timestamp)

    if current_replicas <= 0:
        raise ValueError(f"current_replicas must be > 0, got {current_replicas}")

    details: dict[str, Any] = dict(extra_details or {})
    applied_constraints: list[str] = []
    warnings: list[str] = []

    # Prepare reactive telemetry snapshot model
    reactive_input: ReactiveTelemetryInput | None = None
    if cpu_telemetry is not None:
        reactive_input = ReactiveTelemetryInput(
            is_available=cpu_telemetry.is_available,
            is_fresh=cpu_telemetry.is_fresh,
            pod_count=cpu_telemetry.pod_count,
            current_cpu_utilization_ratio=cpu_telemetry.cpu_utilization_ratio,
            target_cpu_utilization_ratio=config.reactive_cpu_target_ratio,
            total_cpu_cores=cpu_telemetry.total_cpu_cores,
            requested_cpu_cores=cpu_telemetry.requested_cpu_cores,
        )

    # 1. Telemetry availability & freshness gate
    if telemetry is None or telemetry.status in ("unavailable", "malformed"):
        status_str = telemetry.status if telemetry else "unavailable"
        return ArbitrationDecisionRecord(
            timestamp=ts_utc,
            cycle_number=cycle_number,
            current_replicas=current_replicas,
            policy_mode=PolicyMode.NO_RECOMMENDATION,
            primary_reason=DecisionReason.TELEMETRY_UNAVAILABLE,
            telemetry_status=status_str,
            forecast=forecast,
            trust_decision=trust_decision,
            rapid_change_result=rapid_change_result,
            reactive_input=reactive_input,
            direction=ScalingDirection.NO_CHANGE,
            action_executed=False,
            warnings=["Telemetry is unavailable or malformed"],
            details=details,
        )

    if not telemetry.is_fresh or telemetry.status == "stale":
        return ArbitrationDecisionRecord(
            timestamp=ts_utc,
            cycle_number=cycle_number,
            current_replicas=current_replicas,
            policy_mode=PolicyMode.NO_RECOMMENDATION,
            primary_reason=DecisionReason.TELEMETRY_STALE,
            telemetry_status="stale",
            forecast=forecast,
            trust_decision=trust_decision,
            rapid_change_result=rapid_change_result,
            reactive_input=reactive_input,
            direction=ScalingDirection.NO_CHANGE,
            action_executed=False,
            warnings=["Telemetry is stale"],
            details=details,
        )

    if not telemetry.is_sufficient or telemetry.status == "available_insufficient":
        return ArbitrationDecisionRecord(
            timestamp=ts_utc,
            cycle_number=cycle_number,
            current_replicas=current_replicas,
            policy_mode=PolicyMode.NO_RECOMMENDATION,
            primary_reason=DecisionReason.INSUFFICIENT_HISTORY,
            telemetry_status=telemetry.status,
            forecast=forecast,
            trust_decision=trust_decision,
            rapid_change_result=rapid_change_result,
            reactive_input=reactive_input,
            direction=ScalingDirection.NO_CHANGE,
            action_executed=False,
            warnings=["Insufficient telemetry history"],
            details=details,
        )

    telemetry_status = telemetry.status

    # 2. Extract predictive recommendation candidate if trusted
    predictive_rec: int | None = None
    if (
        trust_decision is not None
        and trust_decision.trusted
        and predictive_capacity_result is not None
    ):
        predictive_rec = predictive_capacity_result.unconstrained_required_replicas

    # 3. Compute reactive fallback candidate
    reactive_result = calculate_reactive_fallback(
        cpu_telemetry=cpu_telemetry,
        current_replicas=current_replicas,
        config=config,
        evaluated_at=ts_utc,
    )
    reactive_rec: int | None = (
        reactive_result.raw_unconstrained_replicas if reactive_result.eligible else None
    )

    # 4. Arbitration Mode Selection
    policy_mode: PolicyMode
    selected_unconstrained: int | None
    base_reason: DecisionReason

    # Case A: Rapid-Change Override
    # Rapid change detected AND valid reactive recommendation exists
    if rapid_change_result is not None and rapid_change_result.detected:
        if reactive_result.eligible and reactive_rec is not None:
            policy_mode = PolicyMode.RAPID_CHANGE_OVERRIDE
            selected_unconstrained = reactive_rec
            base_reason = DecisionReason.RAPID_CHANGE_DETECTED
        else:
            # Rapid change detected but reactive fallback CPU telemetry is unavailable or stale
            warnings.append(
                "Rapid change detected but reactive CPU telemetry is unavailable/stale; blocking recommendation"
            )
            return ArbitrationDecisionRecord(
                timestamp=ts_utc,
                cycle_number=cycle_number,
                current_replicas=current_replicas,
                policy_mode=PolicyMode.NO_RECOMMENDATION,
                primary_reason=DecisionReason.RAPID_CHANGE_DETECTED,
                telemetry_status=telemetry_status,
                forecast=forecast,
                trust_decision=trust_decision,
                rapid_change_result=rapid_change_result,
                reactive_input=reactive_input,
                predictive_recommendation=predictive_rec,
                reactive_recommendation=reactive_rec,
                direction=ScalingDirection.NO_CHANGE,
                action_executed=False,
                warnings=warnings,
                details=details,
            )

    # Case B: Predictive Mode
    # Forecast is trusted, rapid change NOT detected, predictive recommendation valid
    elif trust_decision is not None and trust_decision.trusted and predictive_rec is not None:
        policy_mode = PolicyMode.PREDICTIVE
        selected_unconstrained = predictive_rec
        base_reason = DecisionReason.TRUSTED_FORECAST

    # Case C: Reactive Fallback Mode
    # Forecast is untrusted (or unavailable), and valid reactive recommendation exists
    elif reactive_result.eligible and reactive_rec is not None:
        policy_mode = PolicyMode.REACTIVE_FALLBACK
        selected_unconstrained = reactive_rec
        base_reason = DecisionReason.REACTIVE_CPU_UTILIZATION

    # Case D: No Recommendation
    # Forecast untrusted AND reactive fallback unavailable/stale
    else:
        untrusted_reason = (
            DecisionReason.FORECAST_UNTRUSTED
            if (trust_decision and not trust_decision.trusted)
            else DecisionReason.FORECAST_UNAVAILABLE
        )
        return ArbitrationDecisionRecord(
            timestamp=ts_utc,
            cycle_number=cycle_number,
            current_replicas=current_replicas,
            policy_mode=PolicyMode.NO_RECOMMENDATION,
            primary_reason=untrusted_reason,
            telemetry_status=telemetry_status,
            forecast=forecast,
            trust_decision=trust_decision,
            rapid_change_result=rapid_change_result,
            reactive_input=reactive_input,
            predictive_recommendation=predictive_rec,
            reactive_recommendation=reactive_rec,
            direction=ScalingDirection.NO_CHANGE,
            action_executed=False,
            warnings=["No trusted forecast and reactive CPU telemetry unavailable"],
            details=details,
        )

    # 5. Apply Constraints Sequentially
    # 5.1 Replica Bounds
    bounded = selected_unconstrained
    if bounded < config.minimum_replicas:
        bounded = config.minimum_replicas
        applied_constraints.append("minimum_bound")
    if bounded > config.maximum_replicas:
        bounded = config.maximum_replicas
        applied_constraints.append("maximum_bound")

    # 5.2 Step Limits
    step_limited = bounded
    if bounded > current_replicas:
        scale_up_delta = bounded - current_replicas
        if scale_up_delta > config.maximum_scale_up_step:
            step_limited = current_replicas + config.maximum_scale_up_step
            applied_constraints.append("scale_up_step_limited")
    elif bounded < current_replicas:
        scale_down_delta = current_replicas - bounded
        if scale_down_delta > config.maximum_scale_down_step:
            step_limited = current_replicas - config.maximum_scale_down_step
            applied_constraints.append("scale_down_step_limited")

    # 5.3 Downscale Stabilization
    if stabilizer is not None:
        stab_eval = stabilizer.evaluate(
            current_time=ts_utc,
            candidate_replicas=step_limited,
            current_replicas=current_replicas,
        )
        stabilized_rec = stab_eval.stabilized_replicas
        if stab_eval.is_stabilized:
            applied_constraints.append("downscale_stabilized")
    else:
        stabilized_rec = step_limited

    # 5.4 Cooldown
    cooldown_blocked = False
    if cooldown_manager is not None:
        cool_eval = cooldown_manager.evaluate(
            current_time=ts_utc,
            candidate_replicas=stabilized_rec,
            current_replicas=current_replicas,
        )
        cooldown_blocked = cool_eval.is_blocked
        final_rec = cool_eval.effective_replicas
        if cool_eval.is_blocked:
            applied_constraints.append("cooldown_active")
    else:
        final_rec = stabilized_rec

    # 6. Direction
    if final_rec > current_replicas:
        direction = ScalingDirection.SCALE_UP
    elif final_rec < current_replicas:
        direction = ScalingDirection.SCALE_DOWN
    else:
        direction = ScalingDirection.NO_CHANGE

    # 7. Determine dominant decision reason
    if "cooldown_active" in applied_constraints:
        primary_reason = DecisionReason.COOLDOWN_ACTIVE
    elif "downscale_stabilized" in applied_constraints:
        primary_reason = DecisionReason.DOWNSCALE_STABILIZED
    elif "scale_up_step_limited" in applied_constraints:
        primary_reason = DecisionReason.SCALE_UP_STEP_LIMITED
    elif "scale_down_step_limited" in applied_constraints:
        primary_reason = DecisionReason.SCALE_DOWN_STEP_LIMITED
    elif "maximum_bound" in applied_constraints:
        primary_reason = DecisionReason.MAXIMUM_BOUND
    elif "minimum_bound" in applied_constraints:
        primary_reason = DecisionReason.MINIMUM_BOUND
    elif final_rec == current_replicas:
        primary_reason = DecisionReason.NO_CHANGE_REQUIRED
    else:
        primary_reason = base_reason

    # 8. Record decision to stateful managers
    if stabilizer is not None:
        stabilizer.record_recommendation(ts_utc, final_rec)

    if cooldown_manager is not None:
        cooldown_manager.record_decision(ts_utc, final_rec, current_replicas)

    return ArbitrationDecisionRecord(
        timestamp=ts_utc,
        cycle_number=cycle_number,
        current_replicas=current_replicas,
        policy_mode=policy_mode,
        primary_reason=primary_reason,
        telemetry_status=telemetry_status,
        forecast=forecast,
        trust_decision=trust_decision,
        rapid_change_result=rapid_change_result,
        reactive_input=reactive_input,
        predictive_recommendation=predictive_rec,
        reactive_recommendation=reactive_rec,
        selected_unconstrained_recommendation=selected_unconstrained,
        bounded_recommendation=bounded,
        step_limited_recommendation=step_limited,
        cooldown_blocked=cooldown_blocked,
        stabilized_recommendation=stabilized_rec,
        final_recommendation=final_rec,
        direction=direction,
        applied_constraints=applied_constraints,
        action_executed=False,
        warnings=warnings,
        details=details,
    )
