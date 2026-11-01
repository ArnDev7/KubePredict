"""Guarded dry-run decision record and contract (Phase 10A).

Encapsulates deterministic trust evaluation, dry-run capacity recommendation,
and recommendation eligibility. Enforces action_executed=False and zero
actionable recommendation in Phase 10A.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from shared.models import ForecastResult, ForecastTrustDecision, ScalingDecision
from shared.validation import require_timezone_aware


class GuardedDryRunRecord(BaseModel):
    """Typed record of a guarded dry-run controller decision cycle."""

    timestamp: datetime
    cycle_number: int = Field(ge=1)
    forecast: ForecastResult | None = None
    trust_decision: ForecastTrustDecision
    recommendation_eligible: bool
    capacity_recommendation: ScalingDecision | None = None
    actionable_recommendation: None = None  # Strictly absent/None in Phase 10A
    action_executed: bool = False  # Strictly False
    warnings: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_guarded_record(self) -> "GuardedDryRunRecord":
        self.timestamp = require_timezone_aware(self.timestamp)

        if self.action_executed:
            raise ValueError("action_executed must be strictly False in Phase 10A")

        if self.actionable_recommendation is not None:
            raise ValueError("actionable_recommendation must be strictly None in Phase 10A")

        if self.recommendation_eligible and not self.trust_decision.trusted:
            raise ValueError(
                "recommendation_eligible can only be True when trust_decision.trusted is True"
            )

        return self
