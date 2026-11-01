"""Dry-run ScalingDecision builder for KubePredict controller (Phase 9B).

Constructs structured ScalingDecision instances strictly in DRY_RUN mode with
live_scaling_enabled=False and zero action capabilities.
"""

from datetime import datetime
from typing import Any

from controller.capacity import CapacityResult
from shared.enums import ControllerMode
from shared.models import ForecastResult, ScalingDecision
from shared.validation import require_timezone_aware


def build_dry_run_decision(
    timestamp: datetime,
    capacity_result: CapacityResult,
    forecast: ForecastResult | None = None,
    extra_details: dict[str, Any] | None = None,
) -> ScalingDecision:
    """Build a strictly read-only dry-run ScalingDecision record.

    Parameters
    ----------
    timestamp : datetime
        Current UTC timestamp.
    capacity_result : CapacityResult
        Result from deterministic capacity model evaluation.
    forecast : ForecastResult | None
        Optional associated ForecastResult model output.
    extra_details : dict[str, Any] | None
        Optional supplementary operational details.

    Returns
    -------
    ScalingDecision
        Validated ScalingDecision record in DRY_RUN mode.
    """
    ts_utc = require_timezone_aware(timestamp)

    details = dict(capacity_result.calculation_details)
    details["applied_constraints"] = capacity_result.applied_constraints
    details["unconstrained_required_replicas"] = capacity_result.unconstrained_required_replicas
    details["effective_capacity_per_replica"] = capacity_result.effective_capacity_per_replica
    details["action_executed"] = False

    if extra_details:
        details.update(extra_details)

    return ScalingDecision(
        timestamp=ts_utc,
        mode=ControllerMode.DRY_RUN,
        current_replicas=capacity_result.current_replicas,
        proposed_replicas=capacity_result.proposed_replicas,
        final_replicas=capacity_result.final_replicas,
        direction=capacity_result.direction,
        reason=capacity_result.reason,
        confidence=None,  # Not fabricated in Phase 9B; deferred to Phase 10
        live_scaling_enabled=False,  # Strictly false in dry-run mode
        forecast=forecast,
        details=details,
    )
