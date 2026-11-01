"""Integration tests for guarded dry-run controller (Phase 10A).

Uses mocked Prometheus telemetry and read-only deployment status to verify
end-to-end multi-cycle execution, trust guard decisions, artifact persistence,
and non-mutation guarantees. Does NOT require live Kubernetes cluster.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from prometheus_client import CollectorRegistry

from controller.capacity_config import CapacityConfig
from controller.config import ObserverConfig
from controller.forecast_store import ForecastStore
from controller.guarded_dry_run import (
    execute_guarded_dry_run_cycle,
    write_guarded_dry_run_artifacts,
)
from controller.k8s_reader import DeploymentReplicaStatus, ReadOnlyDeploymentReader
from controller.metrics import GuardedDryRunMetrics
from controller.telemetry import LiveTelemetryReader, TelemetryResult
from controller.trust_config import TrustGuardConfig
from forecasting.service import ForecastingService
from shared.enums import PrimaryErrorMetric, TrustOutcome


@pytest.fixture
def test_configs(tmp_path: Path) -> tuple[ObserverConfig, CapacityConfig, TrustGuardConfig, Path]:
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
    return obs_cfg, cap_cfg, tg_cfg, tmp_path


class TestGuardedDryRunControllerIntegration:
    """End-to-end integration test with mocked telemetry and read-only cluster state."""

    def test_multi_cycle_guarded_run_produces_valid_artifacts(
        self, test_configs: tuple[ObserverConfig, CapacityConfig, TrustGuardConfig, Path]
    ) -> None:
        obs_cfg, cap_cfg, tg_cfg, tmp_path = test_configs
        metrics = GuardedDryRunMetrics(registry=CollectorRegistry(auto_describe=True))
        store = ForecastStore()

        # Mock forecaster service
        forecasting_service = ForecastingService.from_config_file(
            "forecasting/config/final_forecaster.yaml"
        )

        mock_telem_reader = MagicMock(spec=LiveTelemetryReader)
        mock_dep_reader = MagicMock(spec=ReadOnlyDeploymentReader)
        mock_dep_reader.get_replica_status.return_value = DeploymentReplicaStatus(
            desired_replicas=2,
            available_replicas=2,
            ready_replicas=2,
            is_valid=True,
        )

        base_time = datetime.now(UTC)
        cycle_results = []

        # Run 6 cycles:
        # Cycles 1-3: matching in progress, count < 3 -> untrusted (insufficient_residual_history)
        # Cycles 4-6: matched count >= 3 with zero error -> trusted (trusted_eligible)
        for c in range(1, 7):
            curr_time = base_time + timedelta(seconds=15 * c)

            # Telemetry returns historical observations and current rate 10.0
            mock_telem_reader.read_history.return_value = TelemetryResult(
                query_started_at=curr_time - timedelta(seconds=2),
                query_completed_at=curr_time,
                expected_interval_seconds=15.0,
                timestamps=[curr_time - timedelta(seconds=15 * i) for i in range(5, 0, -1)],
                values=[10.0] * 5,
                observation_count=5,
                latest_age_seconds=15.0,
                is_fresh=True,
                is_sufficient=True,
                status="available_fresh",
            )

            res = execute_guarded_dry_run_cycle(
                cycle_number=c,
                current_time=curr_time,
                observer_config=obs_cfg,
                capacity_config=cap_cfg,
                trust_guard_config=tg_cfg,
                telemetry_reader=mock_telem_reader,
                forecasting_service=forecasting_service,
                forecast_store=store,
                deployment_reader=mock_dep_reader,
                metrics=metrics,
            )
            cycle_results.append(res)

        # Cycles 1, 2 should be untrusted due to insufficient residual history
        assert cycle_results[0].trust_decision.trusted is False
        assert cycle_results[0].trust_decision.outcome == TrustOutcome.INSUFFICIENT_RESIDUAL_HISTORY
        assert cycle_results[0].recommendation_eligible is False

        # By cycle 4, 3 residuals have matched with zero error -> trusted
        assert cycle_results[3].trust_decision.trusted is True
        assert cycle_results[3].trust_decision.outcome == TrustOutcome.TRUSTED
        assert cycle_results[3].recommendation_eligible is True
        assert cycle_results[3].decision is not None
        assert cycle_results[3].final_replicas is not None

        # Action executed strictly False across all cycles
        assert all(c.action_executed is False for c in cycle_results)

        # Persist artifacts and verify
        run_dir = tmp_path / "test-guarded-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        conf_file = run_dir / "observer.yaml"
        conf_file.write_text("dummy", encoding="utf-8")
        cap_file = run_dir / "capacity.yaml"
        cap_file.write_text("dummy", encoding="utf-8")
        tg_file = run_dir / "trust_guard.yaml"
        tg_file.write_text("dummy", encoding="utf-8")
        forecaster_file = Path("forecasting/config/final_forecaster.yaml")

        write_guarded_dry_run_artifacts(
            run_dir=run_dir,
            run_id="test-guarded-run",
            observer_config=obs_cfg,
            capacity_config=cap_cfg,
            trust_guard_config=tg_cfg,
            forecasting_service=forecasting_service,
            forecast_store=store,
            cycle_results=cycle_results,
            started_at=base_time,
            completed_at=base_time + timedelta(seconds=90),
            status="completed",
            config_path=conf_file,
            capacity_config_path=cap_file,
            trust_guard_config_path=tg_file,
            frozen_forecaster_path=forecaster_file,
        )

        # Verify all files present
        expected_files = [
            "metadata.json",
            "cycles.jsonl",
            "forecasts.csv",
            "trust_decisions.csv",
            "matched_residuals.csv",
            "guarded_recommendations.csv",
            "skipped_cycles.csv",
            "trust_guard_configuration.yaml",
            "validation_report.json",
        ]
        for ef in expected_files:
            assert (run_dir / ef).exists(), f"Missing expected file: {ef}"

        # Verify validation report
        val_rep = json.loads((run_dir / "validation_report.json").read_text(encoding="utf-8"))
        assert val_rep["passed"] is True
        assert val_rep["action_executed_all_false"] is True
        assert val_rep["no_actionable_recommendations"] is True
        assert val_rep["no_k8s_mutations"] is True

        # Verify metadata counts
        meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        assert meta["trusted_count"] >= 1
        assert meta["untrusted_count"] >= 1
        assert meta["recommendation_eligible_count"] == meta["trusted_count"]
