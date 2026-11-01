"""Unit tests for GuardedDryRunRecord contract (Phase 10A)."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from controller.guarded_decision import GuardedDryRunRecord
from shared.enums import TrustOutcome
from shared.models import ForecastTrustDecision


def make_sample_trust_decision(trusted: bool = True) -> ForecastTrustDecision:
    now = datetime.now(UTC)
    outcome = TrustOutcome.TRUSTED if trusted else TrustOutcome.ABSOLUTE_ERROR_TOO_HIGH
    return ForecastTrustDecision(
        evaluated_at=now,
        trusted=trusted,
        outcome=outcome,
        forecast_id="fc-1",
        model_name="persistence",
        forecast_generated_at=now - timedelta(seconds=15),
        forecast_target_timestamp=now,
        telemetry_latest_timestamp=now,
        telemetry_age_seconds=15.0,
        telemetry_fresh=True,
        history_observations=10,
        minimum_history_required=1,
        matched_residual_count=3,
        minimum_matched_residuals=3,
        residual_window_start=now - timedelta(seconds=180),
        residual_window_end=now,
        recent_mae=0.2 if trusted else 15.0,
        recent_rmse=0.2 if trusted else 15.0,
        recent_smape=2.0 if trusted else 80.0,
        recent_bias=0.0,
        absolute_error_threshold=10.0,
        relative_error_threshold=50.0,
        evidence_window_seconds=180.0,
    )


class TestGuardedDecisionContract:
    """Validate GuardedDryRunRecord schema enforcement and invariant checks."""

    def test_valid_guarded_record_creation(self) -> None:
        now = datetime.now(UTC)
        td = make_sample_trust_decision(trusted=True)
        rec = GuardedDryRunRecord(
            timestamp=now,
            cycle_number=1,
            trust_decision=td,
            recommendation_eligible=True,
            action_executed=False,
        )
        assert rec.action_executed is False
        assert rec.recommendation_eligible is True
        assert rec.actionable_recommendation is None

    def test_action_executed_true_strictly_rejected(self) -> None:
        now = datetime.now(UTC)
        td = make_sample_trust_decision(trusted=True)
        with pytest.raises(ValidationError, match="action_executed must be strictly False"):
            GuardedDryRunRecord(
                timestamp=now,
                cycle_number=1,
                trust_decision=td,
                recommendation_eligible=True,
                action_executed=True,  # Forbidden
            )

    def test_actionable_recommendation_present_rejected(self) -> None:
        now = datetime.now(UTC)
        td = make_sample_trust_decision(trusted=True)
        with pytest.raises(ValidationError):
            GuardedDryRunRecord(
                timestamp=now,
                cycle_number=1,
                trust_decision=td,
                recommendation_eligible=True,
                actionable_recommendation={"replicas": 3},  # type: ignore[arg-type]
                action_executed=False,
            )

    def test_recommendation_eligible_requires_trusted_true(self) -> None:
        now = datetime.now(UTC)
        td_untrusted = make_sample_trust_decision(trusted=False)
        with pytest.raises(
            ValidationError,
            match="recommendation_eligible can only be True when trust_decision.trusted is True",
        ):
            GuardedDryRunRecord(
                timestamp=now,
                cycle_number=1,
                trust_decision=td_untrusted,
                recommendation_eligible=True,  # Invariant violation: eligible but untrusted!
                action_executed=False,
            )

    def test_forbids_unknown_or_future_fields(self) -> None:
        now = datetime.now(UTC)
        td = make_sample_trust_decision(trusted=True)
        # Extra fields representing premature features
        with pytest.raises(ValidationError):
            GuardedDryRunRecord(
                timestamp=now,
                cycle_number=1,
                trust_decision=td,
                recommendation_eligible=True,
                action_executed=False,
                reactive_fallback=True,  # type: ignore[call-arg]
            )

        with pytest.raises(ValidationError):
            GuardedDryRunRecord(
                timestamp=now,
                cycle_number=1,
                trust_decision=td,
                recommendation_eligible=True,
                action_executed=False,
                cooldown_remaining=10.0,  # type: ignore[call-arg]
            )
