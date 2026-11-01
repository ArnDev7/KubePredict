"""Reactive fallback scaling recommendation for KubePredict controller (Phase 10B).

Computes an HPA-style proportional replica recommendation based on read-only CPU
utilization relative to configured target ratio when forecast trust fails or rapid
change occurs. Approximates CPU-based reactive behavior for policy arbitration;
does not replace the Phase 5 HorizontalPodAutoscaler baseline or mutate cluster state.
"""

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from controller.cpu_telemetry import CpuTelemetryResult
from controller.policy_config import PolicyConfig
from shared.enums import DecisionReason, ScalingDirection
from shared.validation import require_timezone_aware


@dataclass(frozen=True)
class ReactiveFallbackResult:
    """Structured result of pure deterministic reactive fallback calculation."""

    evaluated_at: datetime
    eligible: bool
    current_replicas: int
    current_cpu_utilization_ratio: float | None
    target_cpu_utilization_ratio: float
    raw_unconstrained_replicas: int | None
    bounded_replicas: int | None
    final_replicas: int | None
    direction: ScalingDirection
    reason: DecisionReason
    applied_constraints: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def calculate_reactive_fallback(
    cpu_telemetry: CpuTelemetryResult | None,
    current_replicas: int,
    config: PolicyConfig,
    evaluated_at: datetime | None = None,
) -> ReactiveFallbackResult:
    """Compute deterministic reactive replica recommendation from CPU utilization.

    Parameters
    ----------
    cpu_telemetry : CpuTelemetryResult | None
        Recent read-only CPU telemetry snapshot.
    current_replicas : int
        Current replica count (must be > 0).
    config : PolicyConfig
        Validated policy configuration containing CPU target, bounds, and step limits.
    evaluated_at : datetime | None
        Timestamp of evaluation (defaults to UTC now).

    Returns
    -------
    ReactiveFallbackResult
        Deterministic scaling recommendation, applied constraints, and details.
    """
    eval_time = require_timezone_aware(evaluated_at or datetime.now(UTC))

    if current_replicas <= 0:
        raise ValueError(f"current_replicas must be > 0, got {current_replicas}")

    target_ratio = config.reactive_cpu_target_ratio

    # 1. Check for missing or unavailable CPU telemetry
    if cpu_telemetry is None or not cpu_telemetry.is_available:
        msg = (
            cpu_telemetry.error_message
            if cpu_telemetry and cpu_telemetry.error_message
            else "CPU telemetry is unavailable or None"
        )
        return ReactiveFallbackResult(
            evaluated_at=eval_time,
            eligible=False,
            current_replicas=current_replicas,
            current_cpu_utilization_ratio=None,
            target_cpu_utilization_ratio=target_ratio,
            raw_unconstrained_replicas=None,
            bounded_replicas=None,
            final_replicas=None,
            direction=ScalingDirection.NO_CHANGE,
            reason=DecisionReason.TELEMETRY_UNAVAILABLE,
            warnings=[msg],
        )

    # 2. Check for stale CPU telemetry
    if not cpu_telemetry.is_fresh:
        return ReactiveFallbackResult(
            evaluated_at=eval_time,
            eligible=False,
            current_replicas=current_replicas,
            current_cpu_utilization_ratio=cpu_telemetry.cpu_utilization_ratio,
            target_cpu_utilization_ratio=target_ratio,
            raw_unconstrained_replicas=None,
            bounded_replicas=None,
            final_replicas=None,
            direction=ScalingDirection.NO_CHANGE,
            reason=DecisionReason.TELEMETRY_STALE,
            warnings=["CPU telemetry is stale"],
        )

    cpu_ratio = cpu_telemetry.cpu_utilization_ratio
    # 3. Validate CPU ratio: must be finite, non-negative, and NOT converted to zero if missing
    if cpu_ratio is None or not math.isfinite(cpu_ratio) or cpu_ratio < 0.0:
        return ReactiveFallbackResult(
            evaluated_at=eval_time,
            eligible=False,
            current_replicas=current_replicas,
            current_cpu_utilization_ratio=cpu_ratio,
            target_cpu_utilization_ratio=target_ratio,
            raw_unconstrained_replicas=None,
            bounded_replicas=None,
            final_replicas=None,
            direction=ScalingDirection.NO_CHANGE,
            reason=DecisionReason.TELEMETRY_UNAVAILABLE,
            warnings=[f"Invalid CPU utilization ratio: {cpu_ratio}"],
        )

    # 4. Standard HPA-style proportional ceiling formula:
    # raw_replicas = ceil(current_replicas * (current_cpu_utilization_ratio / reactive_cpu_target_ratio))
    raw_replicas = math.ceil(current_replicas * (cpu_ratio / target_ratio))

    applied_constraints: list[str] = []

    # 5. Apply minimum replica bound
    bounded_replicas = raw_replicas
    if bounded_replicas < config.minimum_replicas:
        bounded_replicas = config.minimum_replicas
        applied_constraints.append("minimum_bound")

    # 6. Apply maximum replica bound
    if bounded_replicas > config.maximum_replicas:
        bounded_replicas = config.maximum_replicas
        applied_constraints.append("maximum_bound")

    # 7. Apply step limits relative to current_replicas
    final_replicas = bounded_replicas
    if bounded_replicas > current_replicas:
        delta = bounded_replicas - current_replicas
        if delta > config.maximum_scale_up_step:
            final_replicas = current_replicas + config.maximum_scale_up_step
            applied_constraints.append("scale_up_step_limited")
    elif bounded_replicas < current_replicas:
        delta = current_replicas - bounded_replicas
        if delta > config.maximum_scale_down_step:
            final_replicas = current_replicas - config.maximum_scale_down_step
            applied_constraints.append("scale_down_step_limited")

    # 8. Direction
    if final_replicas > current_replicas:
        direction = ScalingDirection.SCALE_UP
    elif final_replicas < current_replicas:
        direction = ScalingDirection.SCALE_DOWN
    else:
        direction = ScalingDirection.NO_CHANGE

    # 9. Reason
    if "scale_up_step_limited" in applied_constraints:
        reason = DecisionReason.SCALE_UP_STEP_LIMITED
    elif "scale_down_step_limited" in applied_constraints:
        reason = DecisionReason.SCALE_DOWN_STEP_LIMITED
    elif "maximum_bound" in applied_constraints:
        reason = DecisionReason.MAXIMUM_BOUND
    elif "minimum_bound" in applied_constraints:
        reason = DecisionReason.MINIMUM_BOUND
    elif final_replicas == current_replicas:
        reason = DecisionReason.NO_CHANGE_REQUIRED
    else:
        reason = DecisionReason.REACTIVE_CPU_UTILIZATION

    details: dict[str, Any] = {
        "pod_count": cpu_telemetry.pod_count,
        "total_cpu_cores": cpu_telemetry.total_cpu_cores,
        "requested_cpu_cores": cpu_telemetry.requested_cpu_cores,
        "current_cpu_utilization_ratio": cpu_ratio,
        "target_cpu_utilization_ratio": target_ratio,
    }

    return ReactiveFallbackResult(
        evaluated_at=eval_time,
        eligible=True,
        current_replicas=current_replicas,
        current_cpu_utilization_ratio=cpu_ratio,
        target_cpu_utilization_ratio=target_ratio,
        raw_unconstrained_replicas=raw_replicas,
        bounded_replicas=bounded_replicas,
        final_replicas=final_replicas,
        direction=direction,
        reason=reason,
        applied_constraints=applied_constraints,
        warnings=[],
        details=details,
    )
