"""Integration test for observe-only controller end-to-end simulation (Phase 9A)."""

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from controller.cli import _write_run_artifacts
from controller.config import ObserverConfig
from controller.forecast_store import ForecastStore
from controller.metrics import MetricsServer, ObserverMetrics
from controller.observer import ObserverCycleResult, execute_observer_cycle
from controller.telemetry import LiveTelemetryReader
from experiments.telemetry.prometheus_client import (
    MetricSample,
    MetricSeries,
    PrometheusClient,
    RangeQueryResult,
)
from forecasting.service import ForecastingService


@pytest.fixture
def frozen_service() -> ForecastingService:
    conf = {
        "schema_version": "1.0.0",
        "status": "offline_selected",
        "model_family": "persistence",
        "model_name": "persistence",
        "model_version": "1.0.0",
        "parameters": {},
        "sampling_interval_seconds": 15.0,
        "default_controller_horizon_steps": 1,
        "persistence_fallback_enabled": True,
    }
    return ForecastingService(conf)


class TestObserverIntegrationSimulation:
    """End-to-end multi-cycle controller simulation with mocked Prometheus and real artifacts."""

    def test_multi_cycle_observe_run(
        self,
        tmp_path: Path,
        frozen_service: ForecastingService,
    ) -> None:
        run_dir = tmp_path / "observe-simulation-run"
        run_dir.mkdir(parents=True)

        config_path = tmp_path / "observer.yaml"
        config_path.write_text(
            f"""
schema_version: "1.0.0"
mode: "observe"
prometheus_url: "http://127.0.0.1:9090"
query: 'sum(rate(apa_http_requests_total{{job="kubepredict-workload"}}[1m]))'
sampling_interval_seconds: 15.0
history_window_seconds: 180.0
forecast_interval_seconds: 15.0
forecast_horizon_steps: 1
maximum_cycles: 4
output_root: "{tmp_path.as_posix()}"
metrics_port: 9299
""",
            encoding="utf-8",
        )
        cfg = ObserverConfig.from_yaml_file(config_path)

        mock_client = MagicMock(spec=PrometheusClient)
        reader = LiveTelemetryReader(cfg, client=mock_client)
        store = ForecastStore()
        metrics = ObserverMetrics()

        # Start metrics server on isolated port
        server = MetricsServer(host="127.0.0.1", port=9299, registry=metrics.registry)
        server.start()

        try:
            base_time = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
            cycle_results: list[ObserverCycleResult] = []

            # Simulate 4 cycles (advancing 15s each cycle)
            for c_idx in range(1, 5):
                c_time = base_time + timedelta(seconds=(c_idx - 1) * 15)

                if c_idx == 3:
                    # Simulate a stale observation on cycle 3
                    stale_ts = [c_time - timedelta(seconds=120), c_time - timedelta(seconds=90)]
                    stale_samples = [MetricSample(timestamp=t, value=20.0) for t in stale_ts]
                    mock_client.query_range.return_value = RangeQueryResult(
                        field_name="request_rate_per_second",
                        query=cfg.query,
                        result_type="matrix",
                        series=[MetricSeries(labels={}, samples=stale_samples)],
                    )
                else:
                    # Fresh observations advancing chronologically
                    history_points = 5
                    ts_list = [
                        c_time - timedelta(seconds=(history_points - 1 - i) * 15)
                        for i in range(history_points)
                    ]
                    val_list = [20.0 + float(c_idx * 2 + i) for i in range(history_points)]
                    samples = [
                        MetricSample(timestamp=t, value=v)
                        for t, v in zip(ts_list, val_list, strict=True)
                    ]
                    mock_client.query_range.return_value = RangeQueryResult(
                        field_name="request_rate_per_second",
                        query=cfg.query,
                        result_type="matrix",
                        series=[MetricSeries(labels={}, samples=samples)],
                    )

                res = execute_observer_cycle(
                    cycle_number=c_idx,
                    current_time=c_time,
                    config=cfg,
                    telemetry_reader=reader,
                    forecasting_service=frozen_service,
                    forecast_store=store,
                    metrics=metrics,
                )
                cycle_results.append(res)

            # Probe live local metrics endpoint during run
            with httpx.Client(timeout=3.0) as http_client:
                resp = http_client.get("http://127.0.0.1:9299/metrics")
                assert resp.status_code == 200
                assert "kubepredict_observer_cycles_total" in resp.text

        finally:
            server.stop()

        # Write artifacts
        _write_run_artifacts(
            run_dir=run_dir,
            run_id="observe-simulation-run",
            config=cfg,
            forecasting_service=frozen_service,
            forecast_store=store,
            cycle_results=cycle_results,
            started_at=base_time,
            completed_at=base_time + timedelta(seconds=60),
            status="completed",
            config_path=config_path,
            frozen_forecaster_path=Path("forecasting/config/final_forecaster.yaml"),
        )

        # Validate generated evidence files
        assert (run_dir / "metadata.json").exists()
        assert (run_dir / "cycles.jsonl").exists()
        assert (run_dir / "forecasts.csv").exists()
        assert (run_dir / "matched_residuals.csv").exists()
        assert (run_dir / "skipped_cycles.csv").exists()
        assert (run_dir / "validation_report.json").exists()

        # Check metadata content
        meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        assert meta["status"] == "completed"
        assert meta["cycles_executed"] == 4
        assert meta["forecasts_created"] >= 2
        assert (
            meta["forecasts_matched"] >= 1
        )  # At least cycle 1 forecast should be matched by cycle 2!

        # Check matched residuals CSV
        with (run_dir / "matched_residuals.csv").open("r", encoding="utf-8") as f:
            reader_csv = list(csv.DictReader(f))
            assert len(reader_csv) >= 1
            first_m = reader_csv[0]
            assert "forecast_id" in first_m
            assert float(first_m["actual_request_rate_per_second"]) > 0.0
            assert "residual" in first_m
            assert "absolute_error" in first_m

        # Check skipped cycles CSV
        with (run_dir / "skipped_cycles.csv").open("r", encoding="utf-8") as f:
            skipped_csv = list(csv.DictReader(f))
            assert len(skipped_csv) == 1
            assert skipped_csv[0]["cycle_number"] == "3"
            assert "stale" in skipped_csv[0]["outcome"]
