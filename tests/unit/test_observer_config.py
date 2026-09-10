"""Unit tests for ObserverConfig schema and validation (Phase 9A)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from controller.config import FIXED_PROMETHEUS_QUERY, ObserverConfig


class TestObserverConfig:
    """Validate observer configuration rules and boundary enforcement."""

    def test_default_configuration_valid(self) -> None:
        cfg = ObserverConfig()
        assert cfg.mode == "observe"
        assert cfg.prometheus_url == "http://127.0.0.1:9090"
        assert cfg.query == FIXED_PROMETHEUS_QUERY
        assert cfg.sampling_interval_seconds == 15.0
        assert cfg.history_window_seconds == 180.0
        assert cfg.forecast_interval_seconds == 15.0
        assert cfg.forecast_horizon_steps == 1
        assert cfg.maximum_cycles == 10
        assert cfg.maximum_retries == 3
        assert cfg.request_timeout_seconds == 10.0

    def test_rejects_non_observe_mode(self) -> None:
        with pytest.raises(ValidationError, match="controller mode must be strictly 'observe'"):
            ObserverConfig(mode="live")  # type: ignore[arg-type]

        with pytest.raises(ValidationError, match="controller mode must be strictly 'observe'"):
            ObserverConfig(mode="dry_run")  # type: ignore[arg-type]

    def test_rejects_non_localhost_prometheus_url(self) -> None:
        with pytest.raises(ValidationError, match="must target localhost"):
            ObserverConfig(prometheus_url="http://prometheus.monitoring.svc:9090")

        with pytest.raises(ValidationError, match="must target localhost"):
            ObserverConfig(prometheus_url="https://external-cluster.com:9090")

    def test_accepts_valid_localhost_variants(self) -> None:
        cfg1 = ObserverConfig(prometheus_url="http://localhost:9090")
        assert "localhost" in cfg1.prometheus_url

        cfg2 = ObserverConfig(prometheus_url="http://127.0.0.1:19090")
        assert "127.0.0.1" in cfg2.prometheus_url

    def test_rejects_arbitrary_promql_query(self) -> None:
        with pytest.raises(ValidationError, match="arbitrary PromQL is forbidden"):
            ObserverConfig(query="sum(rate(container_cpu_usage_seconds_total[1m]))")

        with pytest.raises(ValidationError, match="arbitrary PromQL is forbidden"):
            ObserverConfig(query="up")

    def test_rejects_excessive_timeout(self) -> None:
        with pytest.raises(ValidationError, match="Input should be less than or equal to 10"):
            ObserverConfig(request_timeout_seconds=15.0)

    def test_rejects_excessive_retries(self) -> None:
        with pytest.raises(ValidationError, match="Input should be less than or equal to 3"):
            ObserverConfig(maximum_retries=5)

    def test_rejects_non_positive_cycles(self) -> None:
        with pytest.raises(ValidationError, match="Input should be greater than 0"):
            ObserverConfig(maximum_cycles=0)

    def test_rejects_history_window_smaller_than_sampling_interval(self) -> None:
        with pytest.raises(ValidationError, match="history_window_seconds.*must be >="):
            ObserverConfig(
                sampling_interval_seconds=30.0,
                history_window_seconds=15.0,
            )

    def test_rejects_boolean_for_numeric_fields(self) -> None:
        with pytest.raises(ValidationError, match="Boolean values are not accepted"):
            ObserverConfig(maximum_cycles=True)  # type: ignore[arg-type]

        with pytest.raises(ValidationError, match="Boolean values are not accepted"):
            ObserverConfig(maximum_retries=False)  # type: ignore[arg-type]

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            ObserverConfig(enable_live_scaling=True)  # type: ignore[call-arg]

    def test_load_from_yaml_file(self, tmp_path: Path) -> None:
        yaml_content = """
schema_version: "1.0.0"
mode: "observe"
prometheus_url: "http://127.0.0.1:9090"
query: 'sum(rate(apa_http_requests_total{job="kubepredict-workload"}[1m]))'
sampling_interval_seconds: 15.0
history_window_seconds: 120.0
forecast_interval_seconds: 15.0
forecast_horizon_steps: 1
maximum_cycles: 5
"""
        cfg_file = tmp_path / "test_observer.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")

        loaded = ObserverConfig.from_yaml_file(cfg_file)
        assert loaded.maximum_cycles == 5
        assert loaded.history_window_seconds == 120.0
