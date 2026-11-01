"""Typed arbitration decision contract for KubePredict controller (Phase 10B).

Enforces safety invariants, strict action_executed=False, consistency between
policy mode and recommendations, and complete audit provenance.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from controller.rapid_change import RapidChangeResult
from shared.enums import DecisionReason, PolicyMode, ScalingDirection
from shared.models import ForecastResult, ForecastTrustDecision
from shared.validation import require_timezone_aware


class ReactiveTelemetryInput(BaseModel):
    """Snapshot of reactive CPU telemetry input used in arbitration."""

    is_available: bool
    is_fresh: bool
    pod_count: int
    current_cpu_utilization_ratio: float | None = None
    target_cpu_utilization_ratio: float
    total_cpu_cores: float | None = None
    requested_cpu_cores: float | None = None

    model_config = {"extra": "forbid"}


class ArbitrationDecisionRecord(BaseModel):
    """Typed record of an arbitrated dry-run autoscaling policy decision."""

    timestamp: datetime
    cycle_number: int = Field(ge=1)
    current_replicas: int = Field(gt=0)
    policy_mode: PolicyMode
    primary_reason: DecisionReason
    telemetry_status: str = Field(min_length=1)
    forecast: ForecastResult | None = None
    trust_decision: ForecastTrustDecision | None = None
    rapid_change_result: RapidChangeResult | None = None
    reactive_input: ReactiveTelemetryInput | None = None
    predictive_recommendation: int | None = Field(default=None, gt=0)
    reactive_recommendation: int | None = Field(default=None, gt=0)
    selected_unconstrained_recommendation: int | None = Field(default=None, ge=0)
    bounded_recommendation: int | None = Field(default=None, gt=0)
    step_limited_recommendation: int | None = Field(default=None, gt=0)
    cooldown_blocked: bool = False
    stabilized_recommendation: int | None = Field(default=None, gt=0)
    final_recommendation: int | None = Field(default=None, gt=0)
    direction: ScalingDirection
    applied_constraints: list[str] = Field(default_factory=list)
    action_executed: bool = False  # Strictly False in Phase 10B
    warnings: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_arbitration_record(self) -> "ArbitrationDecisionRecord":
        self.timestamp = require_timezone_aware(self.timestamp)

        # 1. Action executed must strictly be False
        if self.action_executed:
            raise ValueError("action_executed must be strictly False in Phase 10B")

        # 2. Mode consistency rules
        if self.policy_mode == PolicyMode.PREDICTIVE:
            if self.trust_decision is None or not self.trust_decision.trusted:
                raise ValueError(
                    "predictive mode requires trusted forecast (trust_decision.trusted=True)"
                )
            if self.rapid_change_result is not None and self.rapid_change_result.detected:
                raise ValueError("predictive mode cannot be selected when rapid change is detected")
            if self.final_recommendation is None:
                raise ValueError("predictive mode requires non-null final_recommendation")

        elif self.policy_mode == PolicyMode.REACTIVE_FALLBACK:
            if self.trust_decision is not None and self.trust_decision.trusted:
                raise ValueError("reactive_fallback mode requires untrusted forecast")
            if self.final_recommendation is None:
                raise ValueError("reactive_fallback mode requires non-null final_recommendation")

        elif self.policy_mode == PolicyMode.RAPID_CHANGE_OVERRIDE:
            if self.rapid_change_result is None or not self.rapid_change_result.detected:
                raise ValueError(
                    "rapid_change_override mode requires rapid_change_result.detected=True"
                )
            if self.final_recommendation is None:
                raise ValueError(
                    "rapid_change_override mode requires non-null final_recommendation"
                )

        elif self.policy_mode == PolicyMode.NO_RECOMMENDATION:
            if self.final_recommendation is not None:
                raise ValueError("no_recommendation mode requires final_recommendation to be None")
            if self.direction != ScalingDirection.NO_CHANGE:
                raise ValueError("no_recommendation mode requires direction=NO_CHANGE")

        return self
