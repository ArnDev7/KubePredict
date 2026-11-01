"""Unit tests for ArbitrationMetrics (Phase 10B)."""

from datetime import UTC, datetime

from prometheus_client import CollectorRegistry

from controller.arbitration_decision import (
    ArbitrationDecisionRecord,
    ReactiveTelemetryInput,
)
from controller.metrics import ArbitrationMetrics
from controller.rapid_change import RapidChangeResult
from shared.enums import DecisionReason, PolicyMode, ScalingDirection, TrustOutcome
from shared.models import ForecastTrustDecision


class TestArbitrationMetrics:
    def test_isolated_registry_construction(self) -> None:
        reg = CollectorRegistry()
        metrics = ArbitrationMetrics(registry=reg)
        assert metrics.registry is reg

    def test_record_arbitration_decision_predictive(self) -> None:
        reg = CollectorRegistry()
        metrics = ArbitrationMetrics(registry=reg)
        now = datetime.now(UTC)

        dec = ArbitrationDecisionRecord(
            timestamp=now,
            cycle_number=1,
            current_replicas=2,
            policy_mode=PolicyMode.PREDICTIVE,
            primary_reason=DecisionReason.TRUSTED_FORECAST,
            telemetry_status="available_fresh",
            trust_decision=ForecastTrustDecision(
                evaluated_at=now,
                trusted=True,
                outcome=TrustOutcome.TRUSTED,
                forecast_id="fc-1",
                model_name="persistence",
                telemetry_fresh=True,
                history_observations=5,
                minimum_history_required=1,
                matched_residual_count=5,
                minimum_matched_residuals=3,
                recent_mae=0.5,
                recent_smape=4.0,
                absolute_error_threshold=10.0,
                relative_error_threshold=50.0,
                evidence_window_seconds=180.0,
            ),
            predictive_recommendation=3,
            selected_unconstrained_recommendation=3,
            bounded_recommendation=3,
            step_limited_recommendation=3,
            final_recommendation=3,
            direction=ScalingDirection.SCALE_UP,
            action_executed=False,
        )

        metrics.record_arbitration_decision(dec)

        # Verify gauges and counters
        assert (
            reg.get_sample_value(
                "kubepredict_policy_decisions_total",
                {"mode": "predictive", "reason": "trusted_forecast", "direction": "scale_up"},
            )
            == 1.0
        )
        assert (
            reg.get_sample_value(
                "kubepredict_policy_mode",
                {"mode": "predictive"},
            )
            == 1.0
        )
        assert (
            reg.get_sample_value(
                "kubepredict_policy_mode",
                {"mode": "no_recommendation"},
            )
            == 0.0
        )
        assert reg.get_sample_value("kubepredict_final_dry_run_recommendation") == 3.0

    def test_record_arbitration_decision_rapid_change_override(self) -> None:
        reg = CollectorRegistry()
        metrics = ArbitrationMetrics(registry=reg)
        now = datetime.now(UTC)

        rc = RapidChangeResult(
            evaluated_at=now,
            detected=True,
            growth_ratio=4.5,
            absolute_increase=10.0,
            recent_rate=15.0,
            baseline_rate=5.0,
            sample_count=3,
            reason=DecisionReason.RAPID_CHANGE_DETECTED,
        )

        dec = ArbitrationDecisionRecord(
            timestamp=now,
            cycle_number=2,
            current_replicas=2,
            policy_mode=PolicyMode.RAPID_CHANGE_OVERRIDE,
            primary_reason=DecisionReason.RAPID_CHANGE_DETECTED,
            telemetry_status="available_fresh",
            rapid_change_result=rc,
            reactive_input=ReactiveTelemetryInput(
                is_available=True,
                is_fresh=True,
                pod_count=2,
                current_cpu_utilization_ratio=0.85,
                target_cpu_utilization_ratio=0.70,
            ),
            reactive_recommendation=3,
            selected_unconstrained_recommendation=3,
            bounded_recommendation=3,
            step_limited_recommendation=3,
            final_recommendation=3,
            direction=ScalingDirection.SCALE_UP,
            action_executed=False,
        )

        metrics.record_arbitration_decision(dec)

        assert reg.get_sample_value("kubepredict_rapid_change_detected") == 1.0
        assert reg.get_sample_value("kubepredict_rapid_change_growth_ratio") == 4.5
        assert reg.get_sample_value("kubepredict_reactive_cpu_utilization_ratio") == 0.85

    def test_record_arbitration_decision_no_recommendation(self) -> None:
        reg = CollectorRegistry()
        metrics = ArbitrationMetrics(registry=reg)
        now = datetime.now(UTC)

        dec = ArbitrationDecisionRecord(
            timestamp=now,
            cycle_number=3,
            current_replicas=2,
            policy_mode=PolicyMode.NO_RECOMMENDATION,
            primary_reason=DecisionReason.TELEMETRY_UNAVAILABLE,
            telemetry_status="unavailable",
            direction=ScalingDirection.NO_CHANGE,
            action_executed=False,
        )

        metrics.record_arbitration_decision(dec)

        assert (
            reg.get_sample_value(
                "kubepredict_policy_no_recommendation_total",
                {"reason": "telemetry_unavailable"},
            )
            == 1.0
        )
