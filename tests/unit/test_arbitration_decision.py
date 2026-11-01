"""Unit tests for ArbitrationDecisionRecord contract (Phase 10B)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from controller.arbitration_decision import (
    ArbitrationDecisionRecord,
)
from controller.rapid_change import RapidChangeResult
from shared.enums import DecisionReason, PolicyMode, ScalingDirection, TrustOutcome
from shared.models import ForecastTrustDecision


def _create_trust(trusted: bool = True) -> ForecastTrustDecision:
    now = datetime.now(UTC)
    return ForecastTrustDecision(
        evaluated_at=now,
        trusted=trusted,
        outcome=TrustOutcome.TRUSTED if trusted else TrustOutcome.INSUFFICIENT_RESIDUAL_HISTORY,
        forecast_id="fc-1" if trusted else None,
        model_name="persistence",
        telemetry_fresh=True,
        history_observations=5,
        minimum_history_required=1,
        matched_residual_count=5 if trusted else 0,
        minimum_matched_residuals=3,
        recent_mae=0.5 if trusted else None,
        recent_smape=4.0 if trusted else None,
        absolute_error_threshold=10.0,
        relative_error_threshold=50.0,
        evidence_window_seconds=180.0,
    )


class TestArbitrationDecisionContract:
    def test_valid_predictive_record(self) -> None:
        now = datetime.now(UTC)
        rec = ArbitrationDecisionRecord(
            timestamp=now,
            cycle_number=1,
            current_replicas=2,
            policy_mode=PolicyMode.PREDICTIVE,
            primary_reason=DecisionReason.TRUSTED_FORECAST,
            telemetry_status="available_fresh",
            trust_decision=_create_trust(trusted=True),
            predictive_recommendation=3,
            selected_unconstrained_recommendation=3,
            bounded_recommendation=3,
            step_limited_recommendation=3,
            final_recommendation=3,
            direction=ScalingDirection.SCALE_UP,
            action_executed=False,
        )
        assert rec.policy_mode == PolicyMode.PREDICTIVE
        assert rec.action_executed is False
        assert rec.final_recommendation == 3

    def test_action_executed_true_strictly_rejected(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValidationError, match="action_executed must be strictly False"):
            ArbitrationDecisionRecord(
                timestamp=now,
                cycle_number=1,
                current_replicas=2,
                policy_mode=PolicyMode.PREDICTIVE,
                primary_reason=DecisionReason.TRUSTED_FORECAST,
                telemetry_status="available_fresh",
                trust_decision=_create_trust(trusted=True),
                predictive_recommendation=3,
                final_recommendation=3,
                direction=ScalingDirection.SCALE_UP,
                action_executed=True,  # Forbidden
            )

    def test_predictive_requires_trusted_forecast(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValidationError, match="predictive mode requires trusted forecast"):
            ArbitrationDecisionRecord(
                timestamp=now,
                cycle_number=1,
                current_replicas=2,
                policy_mode=PolicyMode.PREDICTIVE,
                primary_reason=DecisionReason.TRUSTED_FORECAST,
                telemetry_status="available_fresh",
                trust_decision=_create_trust(trusted=False),  # Untrusted!
                predictive_recommendation=3,
                final_recommendation=3,
                direction=ScalingDirection.SCALE_UP,
                action_executed=False,
            )

    def test_reactive_fallback_requires_untrusted_forecast(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(
            ValidationError, match="reactive_fallback mode requires untrusted forecast"
        ):
            ArbitrationDecisionRecord(
                timestamp=now,
                cycle_number=1,
                current_replicas=2,
                policy_mode=PolicyMode.REACTIVE_FALLBACK,
                primary_reason=DecisionReason.REACTIVE_CPU_UTILIZATION,
                telemetry_status="available_fresh",
                trust_decision=_create_trust(trusted=True),  # Trusted -> cannot use fallback!
                reactive_recommendation=3,
                final_recommendation=3,
                direction=ScalingDirection.SCALE_UP,
                action_executed=False,
            )

    def test_rapid_change_override_requires_detected(self) -> None:
        now = datetime.now(UTC)
        rc = RapidChangeResult(
            evaluated_at=now,
            detected=False,  # Not detected!
            sample_count=3,
            reason=DecisionReason.NO_CHANGE_REQUIRED,
        )
        with pytest.raises(ValidationError, match="rapid_change_override mode requires"):
            ArbitrationDecisionRecord(
                timestamp=now,
                cycle_number=1,
                current_replicas=2,
                policy_mode=PolicyMode.RAPID_CHANGE_OVERRIDE,
                primary_reason=DecisionReason.RAPID_CHANGE_DETECTED,
                telemetry_status="available_fresh",
                rapid_change_result=rc,
                reactive_recommendation=4,
                final_recommendation=4,
                direction=ScalingDirection.SCALE_UP,
                action_executed=False,
            )

    def test_no_recommendation_requires_none(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(
            ValidationError, match="no_recommendation mode requires final_recommendation to be None"
        ):
            ArbitrationDecisionRecord(
                timestamp=now,
                cycle_number=1,
                current_replicas=2,
                policy_mode=PolicyMode.NO_RECOMMENDATION,
                primary_reason=DecisionReason.TELEMETRY_UNAVAILABLE,
                telemetry_status="unavailable",
                final_recommendation=2,  # Should be None!
                direction=ScalingDirection.NO_CHANGE,
                action_executed=False,
            )

    def test_extra_fields_forbidden(self) -> None:
        now = datetime.now(UTC)
        raw = {
            "timestamp": now.isoformat(),
            "cycle_number": 1,
            "current_replicas": 2,
            "policy_mode": PolicyMode.NO_RECOMMENDATION.value,
            "primary_reason": DecisionReason.TELEMETRY_UNAVAILABLE.value,
            "telemetry_status": "unavailable",
            "direction": ScalingDirection.NO_CHANGE.value,
            "action_executed": False,
            "confidence": 0.95,  # Forbidden fabricated probability
        }
        with pytest.raises(ValidationError):
            ArbitrationDecisionRecord.model_validate(raw)
