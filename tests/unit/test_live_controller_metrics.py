"""Unit tests for Phase 11 LiveControllerMetrics Prometheus counters and gauges."""

from datetime import UTC, datetime

from prometheus_client import CollectorRegistry

from controller.kubernetes_scale import ScaleWriteResult
from controller.metrics import LiveControllerMetrics


class TestLiveControllerMetrics:
    """Verify fixed-cardinality Prometheus metrics for controller live scaling."""

    def test_record_successful_scale_result(self) -> None:
        reg = CollectorRegistry(auto_describe=True)
        metrics = LiveControllerMetrics(reg)

        now = datetime.now(UTC)
        result = ScaleWriteResult(
            attempted_at=now,
            completed_at=now,
            decision_id="cycle-1-test",
            previous_replicas=2,
            requested_replicas=4,
            observed_replicas_after=4,
            attempted=True,
            succeeded=True,
            rejected=False,
            api_status=200,
            retry_count=1,
            conflict_count=1,
            duration_ms=45.0,
        )

        metrics.record_scale_result(result, policy_mode="predictive", direction="scale_up")

        assert (
            reg.get_sample_value(
                "kubepredict_scaling_attempts_total",
                {"result": "succeeded", "policy_mode": "predictive", "direction": "scale_up"},
            )
            == 1.0
        )
        assert reg.get_sample_value("kubepredict_scaling_previous_replicas") == 2.0
        assert reg.get_sample_value("kubepredict_scaling_requested_replicas") == 4.0
        assert reg.get_sample_value("kubepredict_scaling_observed_replicas") == 4.0
        assert reg.get_sample_value("kubepredict_scaling_api_duration_seconds") == 0.045
        assert reg.get_sample_value("kubepredict_scaling_conflicts_total") == 1.0

    def test_record_rejected_scale_result(self) -> None:
        reg = CollectorRegistry(auto_describe=True)
        metrics = LiveControllerMetrics(reg)

        now = datetime.now(UTC)
        result = ScaleWriteResult(
            attempted_at=now,
            completed_at=now,
            decision_id="cycle-2-test",
            previous_replicas=2,
            requested_replicas=5,
            attempted=False,
            succeeded=False,
            rejected=True,
            rejection_reason="live_scaling_disabled",
        )

        metrics.record_scale_result(result, policy_mode="reactive_fallback", direction="scale_up")

        assert (
            reg.get_sample_value(
                "kubepredict_scaling_attempts_total",
                {"result": "rejected", "policy_mode": "reactive_fallback", "direction": "scale_up"},
            )
            == 1.0
        )
        assert (
            reg.get_sample_value(
                "kubepredict_scaling_rejections_total",
                {"reason": "live_scaling_disabled"},
            )
            == 1.0
        )

    def test_cycle_error_counter(self) -> None:
        reg = CollectorRegistry(auto_describe=True)
        metrics = LiveControllerMetrics(reg)

        metrics.controller_cycle_errors_total.labels(category="telemetry_query").inc()
        metrics.controller_cycle_errors_total.labels(category="telemetry_query").inc()
        metrics.controller_cycle_errors_total.labels(category="forecast_prediction").inc()

        assert (
            reg.get_sample_value(
                "kubepredict_controller_cycle_errors_total",
                {"category": "telemetry_query"},
            )
            == 2.0
        )
        assert (
            reg.get_sample_value(
                "kubepredict_controller_cycle_errors_total",
                {"category": "forecast_prediction"},
            )
            == 1.0
        )

    def test_live_enabled_gauge(self) -> None:
        reg = CollectorRegistry(auto_describe=True)
        metrics = LiveControllerMetrics(reg)

        metrics.controller_live_enabled.set(1.0)
        assert reg.get_sample_value("kubepredict_controller_live_enabled") == 1.0

        metrics.controller_live_enabled.set(0.0)
        assert reg.get_sample_value("kubepredict_controller_live_enabled") == 0.0
