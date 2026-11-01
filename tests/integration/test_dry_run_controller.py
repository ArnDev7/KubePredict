"""Integration tests for dry-run controller end-to-end simulation (Phase 9B)."""

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from controller.capacity_config import CapacityConfig
from controller.cli import _write_dry_run_artifacts
from controller.config import ObserverConfig
from controller.dry_run import DryRunCycleResult, execute_dry_run_cycle
from controller.forecast_store import ForecastStore
from controller.k8s_reader import DeploymentReplicaStatus, ReadOnlyDeploymentReader
from controller.metrics import DryRunMetrics, MetricsServer
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


@pytest.fixture
def test_capacity_config() -> CapacityConfig:
    return CapacityConfig(
        schema_version="1.0.0",
        status="provisional_calibrated",
        capacity_requests_per_second_per_replica=25.0,
        unit="requests_per_second",
        target_utilization_ratio=0.70,
        safety_margin_ratio=0.90,
        minimum_replicas=1,
        maximum_replicas=10,
        maximum_scale_up_step=3,
        maximum_scale_down_step=1,
        minimum_prediction_rate=0.0,
        capacity_source="phase_7b_fixed_baseline_telemetry",
        capacity_evidence_reference="data/raw/scenarios/dataset-index.json",
        workload_profile="cpu_iterations_100k_delay_20ms_payload_50b",
    )


class TestDryRunControllerIntegration:
    """End-to-end dry-run multi-cycle simulation with mocked Prometheus and K8s reader."""

    def test_multi_cycle_dry_run(
        self,
        tmp_path: Path,
        frozen_service: ForecastingService,
        test_capacity_config: CapacityConfig,
    ) -> None:
        run_dir = tmp_path / "dry-run-simulation"
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
metrics_port: 9399
""",
            encoding="utf-8",
        )
        cfg = ObserverConfig.from_yaml_file(config_path)

        cap_config_path = tmp_path / "capacity.yaml"
        cap_config_path.write_text(
            """
schema_version: "1.0.0"
status: "provisional_calibrated"
capacity_requests_per_second_per_replica: 25.0
unit: "requests_per_second"
target_utilization_ratio: 0.70
safety_margin_ratio: 0.90
minimum_replicas: 1
maximum_replicas: 10
maximum_scale_up_step: 3
maximum_scale_down_step: 1
minimum_prediction_rate: 0.0
capacity_source: "phase_7b_fixed_baseline_telemetry"
capacity_evidence_reference: "data/raw/scenarios/dataset-index.json"
workload_profile: "cpu_iterations_100k_delay_20ms_payload_50b"
""",
            encoding="utf-8",
        )

        mock_prom_client = MagicMock(spec=PrometheusClient)
        telemetry_reader = LiveTelemetryReader(cfg, client=mock_prom_client)
        forecast_store = ForecastStore()
        mock_k8s_reader = MagicMock(spec=ReadOnlyDeploymentReader)
        mock_k8s_reader.get_replica_status.return_value = DeploymentReplicaStatus(
            is_valid=True,
            desired_replicas=2,
            available_replicas=2,
            ready_replicas=2,
            error_message=None,
        )

        metrics = DryRunMetrics()
        server = MetricsServer(host="127.0.0.1", port=9399, registry=metrics.registry)
        server.start()

        try:
            base_time = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
            cycle_results: list[DryRunCycleResult] = []

            # 4 cycles:
            # Cycle 1: demand 0.0 -> proposed 1, final 1, dir scale_down, reason minimum_bound
            # Cycle 2: demand 45.0 -> proposed 3, final 3, dir scale_up, reason forecast_demand
            # Cycle 3: demand 100.0 -> proposed 7, final 5, dir scale_up, reason scale_up_step_limited
            # Cycle 4: stale telemetry -> skipped cycle
            demand_values = [0.0, 45.0, 100.0, 0.0]

            for c_idx in range(1, 5):
                c_time = base_time + timedelta(seconds=(c_idx - 1) * 15)

                if c_idx == 4:
                    # Stale observation
                    stale_ts = [c_time - timedelta(seconds=120)]
                    mock_prom_client.query_range.return_value = RangeQueryResult(
                        field_name="request_rate_per_second",
                        query=cfg.query,
                        result_type="matrix",
                        series=[
                            MetricSeries(
                                labels={},
                                samples=[MetricSample(timestamp=stale_ts[0], value=10.0)],
                            )
                        ],
                    )
                else:
                    # Fresh observation
                    val = demand_values[c_idx - 1]
                    fresh_ts = [c_time - timedelta(seconds=15), c_time]
                    mock_prom_client.query_range.return_value = RangeQueryResult(
                        field_name="request_rate_per_second",
                        query=cfg.query,
                        result_type="matrix",
                        series=[
                            MetricSeries(
                                labels={},
                                samples=[MetricSample(timestamp=t, value=val) for t in fresh_ts],
                            )
                        ],
                    )

                res = execute_dry_run_cycle(
                    cycle_number=c_idx,
                    current_time=c_time,
                    observer_config=cfg,
                    capacity_config=test_capacity_config,
                    telemetry_reader=telemetry_reader,
                    forecasting_service=frozen_service,
                    forecast_store=forecast_store,
                    deployment_reader=mock_k8s_reader,
                    metrics=metrics,
                )
                cycle_results.append(res)

            # Check Cycle 1
            assert cycle_results[0].outcome == "dry_run_recommendation_created"
            assert cycle_results[0].current_replicas == 2
            assert cycle_results[0].proposed_replicas == 1
            assert cycle_results[0].final_replicas == 1
            assert cycle_results[0].direction == "scale_down"
            assert cycle_results[0].reason == "minimum_bound"
            assert cycle_results[0].action_executed is False

            # Check Cycle 2
            assert cycle_results[1].outcome == "dry_run_recommendation_created"
            assert cycle_results[1].proposed_replicas == 3
            assert cycle_results[1].final_replicas == 3
            assert cycle_results[1].direction == "scale_up"
            assert cycle_results[1].reason == "forecast_demand"
            assert cycle_results[1].action_executed is False

            # Check Cycle 3
            assert cycle_results[2].outcome == "dry_run_recommendation_created"
            assert cycle_results[2].proposed_replicas == 7
            assert cycle_results[2].final_replicas == 5  # Step limited to +3
            assert cycle_results[2].direction == "scale_up"
            assert cycle_results[2].reason == "scale_up_step_limited"
            assert "scale_up_step_limited" in cycle_results[2].applied_constraints
            assert cycle_results[2].action_executed is False

            # Check Cycle 4
            assert cycle_results[3].outcome == "forecast_skipped_stale_telemetry"
            assert cycle_results[3].decision is None

            # Verify metrics endpoint responds
            resp = httpx.get("http://127.0.0.1:9399/metrics", timeout=5.0)
            assert resp.status_code == 200
            content = resp.text
            assert "kubepredict_dry_run_recommendations_total" in content
            assert 'direction="scale_up"' in content
            assert 'direction="scale_down"' in content
            assert "kubepredict_dry_run_final_replicas" in content

            # Write artifacts
            dummy_forecaster = tmp_path / "final_forecaster.yaml"
            dummy_forecaster.write_text("model_name: persistence\n", encoding="utf-8")

            _write_dry_run_artifacts(
                run_dir=run_dir,
                run_id="dry-run-sim-001",
                observer_config=cfg,
                capacity_config=test_capacity_config,
                forecasting_service=frozen_service,
                forecast_store=forecast_store,
                cycle_results=cycle_results,
                started_at=base_time,
                completed_at=base_time + timedelta(seconds=60),
                status="completed",
                config_path=config_path,
                capacity_config_path=cap_config_path,
                frozen_forecaster_path=dummy_forecaster,
            )

            # Verify artifact integrity
            assert (run_dir / "metadata.json").exists()
            assert (run_dir / "cycles.jsonl").exists()
            assert (run_dir / "forecasts.csv").exists()
            assert (run_dir / "recommendations.csv").exists()
            assert (run_dir / "matched_residuals.csv").exists()
            assert (run_dir / "skipped_cycles.csv").exists()
            assert (run_dir / "capacity_configuration.yaml").exists()
            assert (run_dir / "validation_report.json").exists()

            # Verify recommendations.csv
            with (run_dir / "recommendations.csv").open("r", encoding="utf-8") as f:
                reader = list(csv.DictReader(f))
                assert len(reader) == 3
                for row in reader:
                    assert row["action_executed"] == "False"
                    assert row["current_replicas"] == "2"

            # Verify validation_report.json
            val_rep = json.loads((run_dir / "validation_report.json").read_text(encoding="utf-8"))
            assert val_rep["all_action_executed_false"] is True
            assert val_rep["recommendation_count"] == 3
            assert val_rep["skipped_cycles_count"] == 1
            assert val_rep["zero_kubernetes_writes"] is True

        finally:
            server.stop()
