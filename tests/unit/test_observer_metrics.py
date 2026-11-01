"""Unit tests for ObserverMetrics and MetricsServer (Phase 9A)."""

import httpx
from prometheus_client import CollectorRegistry, generate_latest

from controller.metrics import MetricsServer, ObserverMetrics


class TestObserverMetrics:
    """Validate isolated registry, bounded cardinality, and server operations."""

    def test_isolated_registries_do_not_conflict(self) -> None:
        reg1 = CollectorRegistry()
        reg2 = CollectorRegistry()

        m1 = ObserverMetrics(registry=reg1)
        m2 = ObserverMetrics(registry=reg2)

        m1.record_cycle("forecast_created", 5.0, 10, 1)
        m2.record_cycle("forecast_skipped_stale_telemetry", 45.0, 10, 0)

        # Inspect reg1
        metrics_text1 = generate_latest(reg1).decode("utf-8")
        assert 'outcome="forecast_created"' in metrics_text1
        assert 'outcome="forecast_skipped_stale_telemetry"' not in metrics_text1

        # Inspect reg2
        metrics_text2 = generate_latest(reg2).decode("utf-8")
        assert 'outcome="forecast_skipped_stale_telemetry"' in metrics_text2
        assert 'outcome="forecast_created"' not in metrics_text2

    def test_record_forecast_and_matched(self) -> None:
        reg = CollectorRegistry()
        metrics = ObserverMetrics(registry=reg)

        metrics.record_forecast(model_name="persistence", fallback_used=False, predicted_val=42.5)
        metrics.record_matched(actual_val=45.0, abs_err=2.5)

        collected = list(reg.collect())
        families = {f.name: f for f in collected}
        sample_names = {s.name for f in collected for s in f.samples}
        assert "kubepredict_observer_forecasts_total" in sample_names
        assert "kubepredict_observer_prediction_request_rate" in sample_names
        assert "kubepredict_observer_actual_request_rate" in sample_names
        assert "kubepredict_observer_absolute_error" in sample_names
        assert "kubepredict_observer_matched_forecasts_total" in sample_names

        pred_sample = families["kubepredict_observer_prediction_request_rate"].samples[0]
        assert pred_sample.value == 42.5

        act_sample = families["kubepredict_observer_actual_request_rate"].samples[0]
        assert act_sample.value == 45.0

    def test_metrics_server_start_and_stop(self) -> None:
        reg = CollectorRegistry()
        metrics = ObserverMetrics(registry=reg)
        metrics.record_cycle("forecast_created", 2.0, 5, 1)

        # Use port 9199 for test
        server = MetricsServer(host="127.0.0.1", port=9199, registry=reg)
        try:
            server.start()
            with httpx.Client(timeout=3.0) as client:
                resp = client.get("http://127.0.0.1:9199/metrics")
                assert resp.status_code == 200
                assert "kubepredict_observer_cycles_total" in resp.text
        finally:
            server.stop()

        # Verify server is stopped and port is released
        server.stop()  # Idempotent call
