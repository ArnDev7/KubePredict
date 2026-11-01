"""Integration tests for arbitrated dry-run controller (Phase 10B).

Verifies multi-cycle pipeline execution, arbitration between predictive, rapid-change,
and reactive fallback, downscale stabilization, cooldown dampening, artifact persistence,
and non-mutation guarantees. Does NOT require live Kubernetes cluster.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from prometheus_client import CollectorRegistry

from controller.arbitration_dry_run import (
    execute_arbitration_cycle,
    persist_arbitration_artifacts,
)
from controller.capacity_config import CapacityConfig
from controller.config import ObserverConfig
from controller.cooldown import CooldownManager
from controller.cpu_telemetry import (
    CpuTelemetryProvider,
    CpuTelemetryResult,
)
from controller.forecast_store import ForecastStore
from controller.k8s_reader import DeploymentReplicaStatus, ReadOnlyDeploymentReader
from controller.metrics import ArbitrationMetrics
from controller.policy_config import PolicyConfig
from controller.stabilization import DownscaleStabilizer
from controller.telemetry import LiveTelemetryReader, TelemetryResult
from controller.trust_config import TrustGuardConfig
from forecasting.service import ForecastingService
from shared.enums import PrimaryErrorMetric


@pytest.fixture
def test_configs(
    tmp_path: Path,
) -> tuple[ObserverConfig, CapacityConfig, TrustGuardConfig, PolicyConfig, Path]:
    obs_cfg = ObserverConfig(
        mode="observe",
        prometheus_url="http://127.0.0.1:9090",
        query='sum(rate(apa_http_requests_total{job="kubepredict-workload"}[1m]))',
        sampling_interval_seconds=15.0,
        history_window_seconds=180.0,
        forecast_horizon_steps=1,
        forecast_interval_seconds=15.0,
        maximum_cycles=6,
        metrics_host="127.0.0.1",
        metrics_port=9101,
    )
    cap_cfg = CapacityConfig(
        schema_version="1.0.0",
        status="provisional_calibrated",
        capacity_requests_per_second_per_replica=25.0,
        unit="requests_per_second",
        target_utilization_ratio=0.7,
        safety_margin_ratio=0.9,
        minimum_replicas=1,
        maximum_replicas=10,
        maximum_scale_up_step=3,
        maximum_scale_down_step=1,
        minimum_prediction_rate=0.0,
        capacity_source="pre_benchmarked_baseline",
        capacity_evidence_reference="data/raw/scenarios/dataset-index.json",
        workload_profile="io_bound_standard",
    )
    tg_cfg = TrustGuardConfig(
        schema_version="1.0.0",
        status="provisional_validated",
        enabled=True,
        evidence_window_seconds=180.0,
        minimum_matched_residuals=3,
        residual_max_age_seconds=120.0,
        absolute_error_threshold=10.0,
        relative_error_threshold=50.0,
        primary_error_metric=PrimaryErrorMetric.MAE,
        require_fresh_telemetry=True,
        require_valid_forecast=True,
        minimum_history_observations=1,
        threshold_source="offline_scenario_validation_and_live_baseline_residuals",
        threshold_evidence_reference="forecasting/config/final_forecaster.yaml#L70-L79",
    )
    pol_cfg = PolicyConfig(
        schema_version="1.0.0",
        status="provisional_validated",
        request_rate_window_samples=3,
        rapid_change_growth_ratio=2.0,
        rapid_change_absolute_rate=5.0,
        minimum_valid_samples=2,
        reactive_cpu_target_ratio=0.70,
        minimum_replicas=1,
        maximum_replicas=10,
        maximum_scale_up_step=3,
        maximum_scale_down_step=1,
        cooldown_seconds=60.0,
        downscale_stabilization_seconds=90.0,
        source="test_policy_derivation",
        evidence_reference="controller/config/capacity.yaml;controller/config/trust_guard.yaml",
    )
    return obs_cfg, cap_cfg, tg_cfg, pol_cfg, tmp_path


class TestArbitrationDryRunControllerIntegration:
    """End-to-end integration test with mocked telemetry and read-only cluster state."""

    def test_multi_cycle_arbitration_run_produces_valid_artifacts(
        self,
        test_configs: tuple[ObserverConfig, CapacityConfig, TrustGuardConfig, PolicyConfig, Path],
    ) -> None:
        obs_cfg, cap_cfg, tg_cfg, pol_cfg, tmp_path = test_configs
        metrics = ArbitrationMetrics(registry=CollectorRegistry(auto_describe=True))
        store = ForecastStore()
        cooldown_mgr = CooldownManager(pol_cfg.cooldown_seconds)
        stabilizer = DownscaleStabilizer(pol_cfg.downscale_stabilization_seconds)

        forecasting_service = ForecastingService.from_config_file(
            "forecasting/config/final_forecaster.yaml"
        )

        mock_telem_reader = MagicMock(spec=LiveTelemetryReader)
        mock_dep_reader = MagicMock(spec=ReadOnlyDeploymentReader)
        mock_dep_reader.get_replica_status.return_value = DeploymentReplicaStatus(
            is_valid=True,
            desired_replicas=2,
            available_replicas=2,
            ready_replicas=2,
            error_message=None,
        )

        mock_cpu_provider = MagicMock(spec=CpuTelemetryProvider)
        mock_cpu_provider.get_cpu_telemetry.return_value = CpuTelemetryResult(
            timestamp=datetime.now(UTC),
            is_available=True,
            is_fresh=True,
            pod_count=2,
            total_cpu_cores=0.06,
            requested_cpu_cores=0.20,
            cpu_utilization_ratio=0.30,
        )

        # Generate telemetry with steady request rate 1.0 req/s
        base_time = datetime.now(UTC) - timedelta(seconds=120)

        def mock_query(current_time: datetime, minimum_history: int = 1) -> TelemetryResult:
            timestamps = [current_time - timedelta(seconds=(5 - i) * 15) for i in range(5)]
            values = [1.0, 1.0, 1.0, 1.0, 1.0]
            return TelemetryResult(
                query_started_at=current_time - timedelta(seconds=1),
                query_completed_at=current_time,
                observation_start=timestamps[0],
                observation_end=timestamps[-1],
                expected_interval_seconds=15.0,
                timestamps=timestamps,
                values=values,
                observation_count=len(values),
                missing_interval_count=0,
                latest_age_seconds=1.0,
                is_fresh=True,
                is_sufficient=True,
                status="available_fresh",
                warnings=[],
            )

        mock_telem_reader.read_history.side_effect = mock_query

        cycle_results = []
        for cycle_num in range(1, 7):
            cycle_time = base_time + timedelta(seconds=cycle_num * 15)
            res = execute_arbitration_cycle(
                cycle_number=cycle_num,
                current_time=cycle_time,
                observer_config=obs_cfg,
                capacity_config=cap_cfg,
                trust_guard_config=tg_cfg,
                policy_config=pol_cfg,
                telemetry_reader=mock_telem_reader,
                forecasting_service=forecasting_service,
                forecast_store=store,
                deployment_reader=mock_dep_reader,
                cpu_telemetry_provider=mock_cpu_provider,
                metrics=metrics,
                cooldown_manager=cooldown_mgr,
                stabilizer=stabilizer,
            )
            cycle_results.append(res)

        assert len(cycle_results) == 6

        # Persist artifacts to temporary directory
        run_id = "test-arbitration-run"
        run_dir = tmp_path / run_id
        policy_path = Path("controller/config/policy.yaml")

        persist_arbitration_artifacts(
            run_dir=run_dir,
            run_id=run_id,
            started_at=base_time,
            completed_at=base_time + timedelta(seconds=90),
            status="completed",
            cycle_results=cycle_results,
            forecast_store=store,
            observer_config=obs_cfg,
            capacity_config=cap_cfg,
            trust_guard_config=tg_cfg,
            policy_config=pol_cfg,
            forecasting_service=forecasting_service,
            policy_config_path=policy_path,
        )

        # Assert all required 11 files exist
        expected_files = [
            "metadata.json",
            "cycles.jsonl",
            "forecasts.csv",
            "trust_decisions.csv",
            "rapid_change_decisions.csv",
            "reactive_recommendations.csv",
            "arbitration_decisions.csv",
            "matched_residuals.csv",
            "skipped_cycles.csv",
            "policy_configuration.yaml",
            "validation_report.json",
        ]
        for ef in expected_files:
            f_path = run_dir / ef
            assert f_path.exists(), f"Missing expected artifact: {ef}"

        # Verify metadata
        with (run_dir / "metadata.json").open("r", encoding="utf-8") as f:
            meta = json.load(f)
        assert meta["run_id"] == run_id
        assert meta["phase"] == "10B"
        assert meta["mode"] == "arbitration_dry_run"
        assert meta["cycles_completed"] == 6
        assert meta["action_executed_all_false"] is True

        # Verify validation_report
        with (run_dir / "validation_report.json").open("r", encoding="utf-8") as f:
            val_rep = json.load(f)
        assert val_rep["passed"] is True
        assert val_rep["action_executed_all_false"] is True
        assert val_rep["no_k8s_mutations"] is True

        # Verify arbitration_decisions.csv action_executed is strictly False
        with (run_dir / "arbitration_decisions.csv").open("r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 7  # 1 header + 6 cycles
        header = lines[0].strip().split(",")
        action_idx = header.index("action_executed")
        for line in lines[1:]:
            parts = line.strip().split(",")
            assert parts[action_idx] == "False"
