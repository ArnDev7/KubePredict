"""Pure deterministic capacity model for KubePredict controller (Phase 9B).

Converts predicted request rate into bounded replica recommendations.
Contains zero Kubernetes API access, zero network calls, and zero filesystem writes.
"""

import math
from dataclasses import dataclass, field
from typing import Any

from controller.capacity_config import CapacityConfig
from shared.enums import DecisionReason, ScalingDirection


@dataclass(frozen=True)
class CapacityResult:
    """Structured result of pure deterministic capacity model evaluation."""

    predicted_request_rate_per_second: float
    current_replicas: int
    effective_capacity_per_replica: float
    unconstrained_required_replicas: int
    proposed_replicas: int
    final_replicas: int
    direction: ScalingDirection
    reason: DecisionReason
    applied_constraints: list[str] = field(default_factory=list)
    calculation_details: dict[str, Any] = field(default_factory=dict)


def calculate_capacity_recommendation(
    predicted_request_rate_per_second: float,
    current_replicas: int,
    config: CapacityConfig,
) -> CapacityResult:
    """Compute deterministic replica recommendation from predicted demand.

    Parameters
    ----------
    predicted_request_rate_per_second : float
        Forecasted request rate in requests per second.
    current_replicas : int
        Current number of replicas (must be >= 0).
    config : CapacityConfig
        Validated capacity configuration.

    Returns
    -------
    CapacityResult
        Deterministic scaling recommendation, applied constraints, and details.
    """
    # 1. Validate inputs
    if not math.isfinite(predicted_request_rate_per_second):
        raise ValueError(
            f"predicted_request_rate_per_second must be finite, got {predicted_request_rate_per_second}"
        )
    if predicted_request_rate_per_second < 0.0:
        raise ValueError(
            f"predicted_request_rate_per_second cannot be negative, got {predicted_request_rate_per_second}"
        )
    if current_replicas < 0:
        raise ValueError(f"current_replicas cannot be negative, got {current_replicas}")

    # 2. Compute effective capacity
    effective_capacity = config.effective_capacity_per_replica
    if effective_capacity <= 0.0:
        raise ValueError(f"effective_capacity must be > 0, got {effective_capacity}")

    applied_constraints: list[str] = []

    # 3. Ceiling division for unconstrained demand
    if predicted_request_rate_per_second <= config.minimum_prediction_rate:
        unconstrained_replicas = 0
    else:
        unconstrained_replicas = math.ceil(predicted_request_rate_per_second / effective_capacity)

    # 4. Apply minimum replica bound (no scale to zero)
    bounded_proposed = unconstrained_replicas
    if bounded_proposed < config.minimum_replicas:
        bounded_proposed = config.minimum_replicas
        applied_constraints.append("minimum_bound")

    # 5. Apply maximum replica bound
    if bounded_proposed > config.maximum_replicas:
        bounded_proposed = config.maximum_replicas
        applied_constraints.append("maximum_bound")

    proposed_replicas = bounded_proposed

    # 6. Apply step limits relative to current_replicas
    final_replicas = proposed_replicas
    if proposed_replicas > current_replicas:
        scale_up_delta = proposed_replicas - current_replicas
        if scale_up_delta > config.maximum_scale_up_step:
            final_replicas = current_replicas + config.maximum_scale_up_step
            applied_constraints.append("scale_up_step_limited")
    elif proposed_replicas < current_replicas:
        scale_down_delta = current_replicas - proposed_replicas
        if scale_down_delta > config.maximum_scale_down_step:
            final_replicas = current_replicas - config.maximum_scale_down_step
            applied_constraints.append("scale_down_step_limited")

    # 7. Recalculate direction from current and final replicas
    if final_replicas > current_replicas:
        direction = ScalingDirection.SCALE_UP
    elif final_replicas < current_replicas:
        direction = ScalingDirection.SCALE_DOWN
    else:
        direction = ScalingDirection.NO_CHANGE

    # 8. Determine dominant decision reason
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
        reason = DecisionReason.FORECAST_DEMAND

    details: dict[str, Any] = {
        "raw_demand_ratio": round(predicted_request_rate_per_second / effective_capacity, 4),
        "target_utilization_ratio": config.target_utilization_ratio,
        "safety_margin_ratio": config.safety_margin_ratio,
        "base_capacity_per_replica": config.capacity_requests_per_second_per_replica,
        "minimum_replicas": config.minimum_replicas,
        "maximum_replicas": config.maximum_replicas,
        "maximum_scale_up_step": config.maximum_scale_up_step,
        "maximum_scale_down_step": config.maximum_scale_down_step,
    }

    return CapacityResult(
        predicted_request_rate_per_second=predicted_request_rate_per_second,
        current_replicas=current_replicas,
        effective_capacity_per_replica=effective_capacity,
        unconstrained_required_replicas=unconstrained_replicas,
        proposed_replicas=proposed_replicas,
        final_replicas=final_replicas,
        direction=direction,
        reason=reason,
        applied_constraints=applied_constraints,
        calculation_details=details,
    )
