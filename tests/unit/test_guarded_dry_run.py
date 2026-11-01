"""Unit tests for guarded dry-run cycle flow (Phase 10A)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from prometheus_client import CollectorRegistry

from controller.capacity_config import CapacityConfig
from controller.config import ObserverConfig
from controller.forecast_store import ForecastStore, PendingForecast
from controller.guarded_dry_run import execute_guarded_dry_run_cycle
from controller.k8s_reader import DeploymentReplicaStatus, ReadOnlyDeploymentReader
from controller.metrics import GuardedDryRunMetrics
from controller.telemetry import LiveTelemetryReader, TelemetryResult
from controller.trust_config import TrustGuardConfig
from forecasting.service import ForecastingService
from shared.enums import PrimaryErrorMetric, TrustOutcome


def get_mock_observer_config() -> ObserverConfig:
    return ObserverConfig(
        mode="observe",
        prometheus_url="http://127.0.0.1:9090",
        query='sum(rate(apa_http_requests_total{job="kubepredict-workload"}[1m]))',
        sampling_interval_seconds=15.0,
        history_window_seconds=180.0,
        forecast_horizon_steps=1,
        forecast_interval_seconds=15.0,
        maximum_cycles=10,
        metrics_host="127.0.0.1",
        metrics_port=9101,
    )


def get_mock_capacity_config() -> CapacityConfig:
    return CapacityConfig(
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


def get_mock_trust_guard_config(min_residuals: int = 3) -> TrustGuardConfig:
    return TrustGuardConfig(
        schema_version="1.0.0",
        status="provisional_validated",
        enabled=True,
        evidence_window_seconds=180.0,
        minimum_matched_residuals=min_residuals,
        residual_max_age_seconds=120.0,
        absolute_error_threshold=10.0,
        relative_error_threshold=50.0,
        primary_error_metric=PrimaryErrorMetric.MAE,
        require_fresh_telemetry=True,
        require_valid_forecast=True,
        minimum_history_observations=1,
        threshold_source="offline_scenario_validation_and_live_baseline_residuals",
        threshold_evidence_reference="forecasting/config/final_forecaster.yaml#L70-L79;experiments/output/controller-dry-run/dry-run-20261027_105511/matched_residuals.csv",
    )


class TestGuardedDryRunFlow:
    """Validate guarded dry-run cycle execution rules and safety guarantees."""

    def test_trusted_forecast_permits_dry_run_recommendation(self) -> None:
        now = datetime.now(UTC)
        obs_cfg = get_mock_observer_config()
        cap_cfg = get_mock_capacity_config()
        tg_cfg = get_mock_trust_guard_config(min_residuals=2)
        metrics = GuardedDryRunMetrics(registry=CollectorRegistry(auto_describe=True))
        store = ForecastStore()

        # Seed with 2 matched residuals with low error
        for i in range(1, 3):
            t_ts = now - timedelta(seconds=15 * i)
            store.add(
                PendingForecast(
                    forecast_id=f"seed-{i}",
                    generated_at=t_ts - timedelta(seconds=15),
                    target_timestamp=t_ts,
                    horizon_seconds=15.0,
                    model_name="persistence",
                    predicted_request_rate_per_second=10.0,
                    actual_request_rate_per_second=10.2,
                    residual=0.2,
                    absolute_error=0.2,
                    status="matched",
                    history_count=1,
                )
            )

        # Mock telemetry reader: fresh history
        mock_telem = MagicMock(spec=LiveTelemetryReader)
        mock_telem.read_history.return_value = TelemetryResult(
            query_started_at=now - timedelta(seconds=2),
            query_completed_at=now,
            expected_interval_seconds=15.0,
            timestamps=[now - timedelta(seconds=15)],
            values=[20.0],
            observation_count=1,
            latest_age_seconds=15.0,
            is_fresh=True,
            is_sufficient=True,
            status="available_fresh",
        )

        # Mock forecaster: predict 20.0 req/s
        mock_svc = MagicMock(spec=ForecastingService)
        mock_svc.persistence_fallback_enabled = True
        mock_svc.predict.return_value = MagicMock(
            horizon_seconds=15.0,
            model_name="persistence",
            predicted_request_rate_per_second=20.0,
            fallback_used=False,
            history_count=1,
            warnings=[],
        )

        # Mock deployment reader: 2 replicas available
        mock_dep = MagicMock(spec=ReadOnlyDeploymentReader)
        mock_dep.get_replica_status.return_value = DeploymentReplicaStatus(
            desired_replicas=2,
            available_replicas=2,
            ready_replicas=2,
            is_valid=True,
        )

        res = execute_guarded_dry_run_cycle(
            cycle_number=1,
            current_time=now,
            observer_config=obs_cfg,
            capacity_config=cap_cfg,
            trust_guard_config=tg_cfg,
            telemetry_reader=mock_telem,
            forecasting_service=mock_svc,
            forecast_store=store,
            deployment_reader=mock_dep,
            metrics=metrics,
        )

        assert res.trust_decision.trusted is True
        assert res.recommendation_eligible is True
        assert res.action_executed is False
        assert res.decision is not None
        assert res.final_replicas is not None
        assert res.guarded_record.actionable_recommendation is None

    def test_untrusted_forecast_blocks_recommendation_eligibility(self) -> None:
        now = datetime.now(UTC)
        obs_cfg = get_mock_observer_config()
        cap_cfg = get_mock_capacity_config()
        # Requires 5 residuals, but none exist
        tg_cfg = get_mock_trust_guard_config(min_residuals=5)
        metrics = GuardedDryRunMetrics(registry=CollectorRegistry(auto_describe=True))
        store = ForecastStore()

        mock_telem = MagicMock(spec=LiveTelemetryReader)
        mock_telem.read_history.return_value = TelemetryResult(
            query_started_at=now - timedelta(seconds=2),
            query_completed_at=now,
            expected_interval_seconds=15.0,
            timestamps=[now - timedelta(seconds=15)],
            values=[20.0],
            observation_count=1,
            latest_age_seconds=15.0,
            is_fresh=True,
            is_sufficient=True,
            status="available_fresh",
        )

        mock_svc = MagicMock(spec=ForecastingService)
        mock_svc.persistence_fallback_enabled = True
        mock_svc.predict.return_value = MagicMock(
            horizon_seconds=15.0,
            model_name="persistence",
            predicted_request_rate_per_second=20.0,
            fallback_used=False,
            history_count=1,
            warnings=[],
        )

        mock_dep = MagicMock(spec=ReadOnlyDeploymentReader)

        res = execute_guarded_dry_run_cycle(
            cycle_number=1,
            current_time=now,
            observer_config=obs_cfg,
            capacity_config=cap_cfg,
            trust_guard_config=tg_cfg,
            telemetry_reader=mock_telem,
            forecasting_service=mock_svc,
            forecast_store=store,
            deployment_reader=mock_dep,
            metrics=metrics,
        )

        assert res.trust_decision.trusted is False
        assert res.trust_decision.outcome == TrustOutcome.INSUFFICIENT_RESIDUAL_HISTORY
        assert res.recommendation_eligible is False
        assert res.decision is None
        assert res.final_replicas is None
        assert res.action_executed is False
        assert res.guarded_record.actionable_recommendation is None
        # Capacity model was NOT called
        mock_dep.get_replica_status.assert_not_called()

    def test_untrusted_high_error_does_not_invoke_reactive_fallback(self) -> None:
        now = datetime.now(UTC)
        obs_cfg = get_mock_observer_config()
        cap_cfg = get_mock_capacity_config()
        tg_cfg = get_mock_trust_guard_config(min_residuals=2)
        tg_cfg.absolute_error_threshold = 5.0
        metrics = GuardedDryRunMetrics(registry=CollectorRegistry(auto_describe=True))
        store = ForecastStore()

        # Seed with high error residuals (MAE = 20.0 > 5.0)
        for i in range(1, 3):
            t_ts = now - timedelta(seconds=15 * i)
            store.add(
                PendingForecast(
                    forecast_id=f"err-{i}",
                    generated_at=t_ts - timedelta(seconds=15),
                    target_timestamp=t_ts,
                    horizon_seconds=15.0,
                    model_name="persistence",
                    predicted_request_rate_per_second=10.0,
                    actual_request_rate_per_second=30.0,
                    residual=20.0,
                    absolute_error=20.0,
                    status="matched",
                    history_count=1,
                )
            )

        mock_telem = MagicMock(spec=LiveTelemetryReader)
        mock_telem.read_history.return_value = TelemetryResult(
            query_started_at=now - timedelta(seconds=2),
            query_completed_at=now,
            expected_interval_seconds=15.0,
            timestamps=[now - timedelta(seconds=15)],
            values=[10.0],
            observation_count=1,
            latest_age_seconds=15.0,
            is_fresh=True,
            is_sufficient=True,
            status="available_fresh",
        )

        mock_svc = MagicMock(spec=ForecastingService)
        mock_svc.persistence_fallback_enabled = True
        mock_svc.predict.return_value = MagicMock(
            horizon_seconds=15.0,
            model_name="persistence",
            predicted_request_rate_per_second=10.0,
            fallback_used=False,
            history_count=1,
            warnings=[],
        )

        mock_dep = MagicMock(spec=ReadOnlyDeploymentReader)

        res = execute_guarded_dry_run_cycle(
            cycle_number=1,
            current_time=now,
            observer_config=obs_cfg,
            capacity_config=cap_cfg,
            trust_guard_config=tg_cfg,
            telemetry_reader=mock_telem,
            forecasting_service=mock_svc,
            forecast_store=store,
            deployment_reader=mock_dep,
            metrics=metrics,
        )

        assert res.trust_decision.trusted is False
        assert res.recommendation_eligible is False
        assert res.decision is None
        assert res.final_replicas is None
        # Zero reactive fallback invocation or fallback fields
        dump = res.model_dump()
        assert "reactive_fallback" not in dump
        assert "burst" not in dump
        assert "drift" not in dump
        assert "cooldown" not in dump
