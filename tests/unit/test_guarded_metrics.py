"""Unit tests for GuardedDryRunMetrics (Phase 10A)."""

from datetime import UTC, datetime

from prometheus_client import CollectorRegistry, generate_latest

from controller.metrics import GuardedDryRunMetrics
from shared.enums import TrustOutcome
from shared.models import ForecastTrustDecision


def make_decision(trusted: bool = True) -> ForecastTrustDecision:
    now = datetime.now(UTC)
    outcome = TrustOutcome.TRUSTED if trusted else TrustOutcome.ABSOLUTE_ERROR_TOO_HIGH
    return ForecastTrustDecision(
        evaluated_at=now,
        trusted=trusted,
        outcome=outcome,
        forecast_id="fc-sample",
        model_name="persistence",
        telemetry_fresh=True,
        history_observations=5,
        minimum_history_required=1,
        matched_residual_count=3,
        minimum_matched_residuals=3,
        recent_mae=0.5 if trusted else 15.0,
        recent_rmse=0.6 if trusted else 16.0,
        recent_smape=5.0 if trusted else 60.0,
        recent_bias=0.1 if trusted else 2.0,
        absolute_error_threshold=10.0,
        relative_error_threshold=50.0,
        evidence_window_seconds=180.0,
        details={"residual_window": {"latest_residual_age_seconds": 15.0}},
    )


class TestGuardedMetrics:
    """Validate fixed-cardinality Prometheus metrics for Forecast Trust Guard."""

    def test_isolated_registry_construction(self) -> None:
        reg1 = CollectorRegistry(auto_describe=True)
        m1 = GuardedDryRunMetrics(registry=reg1)
        reg2 = CollectorRegistry(auto_describe=True)
        m2 = GuardedDryRunMetrics(registry=reg2)

        m1.guard_trusted_state.set(1.0)
        assert m1.guard_trusted_state._value.get() == 1.0
        assert m2.guard_trusted_state._value.get() == 0.0

    def test_record_guard_decision_trusted(self) -> None:
        reg = CollectorRegistry(auto_describe=True)
        metrics = GuardedDryRunMetrics(registry=reg)

        td = make_decision(trusted=True)
        metrics.record_guard_decision(decision=td, recommendation_eligible=True)

        assert metrics.guard_trusted_state._value.get() == 1.0
        assert metrics.guard_recommendation_eligible._value.get() == 1.0
        assert metrics.guard_matched_residuals._value.get() == 3
        assert metrics.guard_recent_mae._value.get() == 0.5
        assert metrics.guard_recent_rmse._value.get() == 0.6
        assert metrics.guard_recent_smape._value.get() == 5.0
        assert metrics.guard_recent_bias._value.get() == 0.1
        assert metrics.guard_residual_age_seconds._value.get() == 15.0

    def test_record_guard_decision_untrusted(self) -> None:
        reg = CollectorRegistry(auto_describe=True)
        metrics = GuardedDryRunMetrics(registry=reg)

        td = make_decision(trusted=False)
        metrics.record_guard_decision(decision=td, recommendation_eligible=False)

        assert metrics.guard_trusted_state._value.get() == 0.0
        assert metrics.guard_recommendation_eligible._value.get() == 0.0

    def test_fixed_cardinality_labels_only(self) -> None:
        reg = CollectorRegistry(auto_describe=True)
        metrics = GuardedDryRunMetrics(registry=reg)

        td = make_decision(trusted=True)
        metrics.record_guard_decision(decision=td, recommendation_eligible=True)

        output = generate_latest(reg).decode("utf-8")
        # Check label names
        assert 'outcome="trusted"' in output
        assert 'trusted="true"' in output
        # Verify no forbidden high-cardinality labels
        assert "forecast_id=" not in output
        assert "timestamp=" not in output
        assert "cycle=" not in output
