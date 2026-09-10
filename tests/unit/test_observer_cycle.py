"""Unit tests for observe-only controller cycle execution (Phase 9A)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from controller.config import ObserverConfig
from controller.forecast_store import ForecastStore, PendingForecast
from controller.metrics import ObserverMetrics
from controller.observer import execute_observer_cycle
from controller.telemetry import LiveTelemetryReader, TelemetryResult
from forecasting.service import ForecastingService


class TestObserverCycle:
    """Validate single controller cycle execution logic and guarantees."""

    @pytest.fixture
    def config(self) -> ObserverConfig:
        return ObserverConfig(
            sampling_interval_seconds=15.0,
            history_window_seconds=180.0,
            forecast_horizon_steps=1,
        )

    @pytest.fixture
    def frozen_service(self) -> ForecastingService:
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

    def test_cycle_creates_forecast_on_fresh_history(
        self,
        config: ObserverConfig,
        frozen_service: ForecastingService,
    ) -> None:
        reader = MagicMock(spec=LiveTelemetryReader)
        store = ForecastStore()
        metrics = ObserverMetrics()

        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        timestamps = [now - timedelta(seconds=(3 - i) * 15) for i in range(4)]
        values = [10.0, 15.0, 20.0, 25.0]

        reader.read_history.return_value = TelemetryResult(
            query_started_at=now,
            query_completed_at=now,
            observation_start=timestamps[0],
            observation_end=timestamps[-1],
            expected_interval_seconds=15.0,
            timestamps=timestamps,
            values=values,
            observation_count=4,
            latest_age_seconds=0.0,
            is_fresh=True,
            is_sufficient=True,
            status="available_fresh",
        )

        res = execute_observer_cycle(
            cycle_number=1,
            current_time=now,
            config=config,
            telemetry_reader=reader,
            forecasting_service=frozen_service,
            forecast_store=store,
            metrics=metrics,
        )

        assert res.outcome == "forecast_created"
        assert res.forecast_id is not None
        assert res.predicted_request_rate == 25.0
        assert res.fallback_used is False
        assert len(store.get_pending()) == 1

    def test_cycle_skips_when_telemetry_stale(
        self,
        config: ObserverConfig,
        frozen_service: ForecastingService,
    ) -> None:
        reader = MagicMock(spec=LiveTelemetryReader)
        store = ForecastStore()
        metrics = ObserverMetrics()

        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        reader.read_history.return_value = TelemetryResult(
            query_started_at=now,
            query_completed_at=now,
            expected_interval_seconds=15.0,
            timestamps=[now - timedelta(seconds=60)],
            values=[10.0],
            observation_count=1,
            latest_age_seconds=60.0,
            is_fresh=False,
            is_sufficient=True,
            status="stale",
            warnings=["Latest observation is stale"],
        )

        res = execute_observer_cycle(
            cycle_number=2,
            current_time=now,
            config=config,
            telemetry_reader=reader,
            forecasting_service=frozen_service,
            forecast_store=store,
            metrics=metrics,
        )

        assert res.outcome == "forecast_skipped_stale_telemetry"
        assert res.forecast_id is None
        assert "stale" in (res.skip_reason or "").lower()
        assert len(store.get_pending()) == 0

    def test_cycle_skips_when_telemetry_unavailable(
        self,
        config: ObserverConfig,
        frozen_service: ForecastingService,
    ) -> None:
        reader = MagicMock(spec=LiveTelemetryReader)
        store = ForecastStore()
        metrics = ObserverMetrics()

        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        reader.read_history.return_value = TelemetryResult(
            query_started_at=now,
            query_completed_at=now,
            expected_interval_seconds=15.0,
            timestamps=[],
            values=[],
            observation_count=0,
            latest_age_seconds=float("inf"),
            is_fresh=False,
            is_sufficient=False,
            status="unavailable",
            warnings=["Prometheus unreachable"],
        )

        res = execute_observer_cycle(
            cycle_number=3,
            current_time=now,
            config=config,
            telemetry_reader=reader,
            forecasting_service=frozen_service,
            forecast_store=store,
            metrics=metrics,
        )

        assert res.outcome == "forecast_skipped_unavailable"
        assert res.forecast_id is None

    def test_cycle_matches_prior_pending_forecast(
        self,
        config: ObserverConfig,
        frozen_service: ForecastingService,
    ) -> None:
        reader = MagicMock(spec=LiveTelemetryReader)
        store = ForecastStore()
        metrics = ObserverMetrics()

        gen_time = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        target_time = gen_time + timedelta(seconds=15)

        # Pre-seed a pending forecast
        prior_pf = PendingForecast(
            forecast_id="prior-1",
            generated_at=gen_time,
            target_timestamp=target_time,
            horizon_seconds=15.0,
            model_name="persistence",
            predicted_request_rate_per_second=20.0,
            history_count=3,
        )
        store.add(prior_pf)

        # Current time reaches target_time (15s later)
        now = target_time
        timestamps = [target_time]
        values = [22.0]

        reader.read_history.return_value = TelemetryResult(
            query_started_at=now,
            query_completed_at=now,
            observation_start=target_time,
            observation_end=target_time,
            expected_interval_seconds=15.0,
            timestamps=timestamps,
            values=values,
            observation_count=1,
            latest_age_seconds=0.0,
            is_fresh=True,
            is_sufficient=True,
            status="available_fresh",
        )

        res = execute_observer_cycle(
            cycle_number=4,
            current_time=now,
            config=config,
            telemetry_reader=reader,
            forecasting_service=frozen_service,
            forecast_store=store,
            metrics=metrics,
        )

        assert res.matched_forecasts_count == 1
        assert prior_pf.status == "matched"
        assert prior_pf.actual_request_rate_per_second == 22.0
        assert prior_pf.residual == 2.0  # 22.0 - 20.0
        assert prior_pf.absolute_error == 2.0
