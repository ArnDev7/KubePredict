"""Unit tests for PendingForecast and ForecastStore (Phase 9A)."""

import math
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from controller.forecast_store import ForecastStore, PendingForecast


class TestForecastStore:
    """Validate lifecycle and residual matching in ForecastStore."""

    def test_create_pending_forecast(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        target = now + timedelta(seconds=15)
        pf = PendingForecast(
            forecast_id="fc-1",
            generated_at=now,
            target_timestamp=target,
            horizon_seconds=15.0,
            model_name="persistence",
            predicted_request_rate_per_second=25.0,
            history_count=5,
        )
        assert pf.status == "pending"
        assert pf.residual is None
        assert pf.actual_request_rate_per_second is None

    def test_rejects_target_not_after_generated(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValidationError, match="must be strictly after"):
            PendingForecast(
                generated_at=now,
                target_timestamp=now,  # Identical
                horizon_seconds=15.0,
                model_name="persistence",
                predicted_request_rate_per_second=25.0,
                history_count=5,
            )

    def test_duplicate_id_rejected(self) -> None:
        store = ForecastStore()
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        target = now + timedelta(seconds=15)
        pf1 = PendingForecast(
            forecast_id="dup-1",
            generated_at=now,
            target_timestamp=target,
            horizon_seconds=15.0,
            model_name="persistence",
            predicted_request_rate_per_second=25.0,
            history_count=5,
        )
        store.add(pf1)
        with pytest.raises(ValueError, match="Duplicate forecast ID"):
            store.add(pf1)

    def test_does_not_match_before_target_time(self) -> None:
        store = ForecastStore()
        gen_time = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        target_time = gen_time + timedelta(seconds=30)
        pf = PendingForecast(
            forecast_id="fc-future",
            generated_at=gen_time,
            target_timestamp=target_time,
            horizon_seconds=30.0,
            model_name="persistence",
            predicted_request_rate_per_second=20.0,
            history_count=5,
        )
        store.add(pf)

        # Current time is 15s after generation (before target time 30s)
        curr_time = gen_time + timedelta(seconds=15)
        matched = store.match_actuals(
            current_time=curr_time,
            observation_timestamps=[curr_time],
            observation_values=[22.0],
        )
        assert len(matched) == 0
        assert pf.status == "pending"

    def test_matches_after_target_time_within_tolerance(self) -> None:
        store = ForecastStore()
        gen_time = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        target_time = gen_time + timedelta(seconds=15)
        pf = PendingForecast(
            forecast_id="fc-match",
            generated_at=gen_time,
            target_timestamp=target_time,
            horizon_seconds=15.0,
            model_name="persistence",
            predicted_request_rate_per_second=30.0,
            history_count=5,
        )
        store.add(pf)

        # Current time has reached target_time
        curr_time = target_time
        matched = store.match_actuals(
            current_time=curr_time,
            observation_timestamps=[target_time],
            observation_values=[35.0],
            tolerance_seconds=15.0,
        )
        assert len(matched) == 1
        assert matched[0].status == "matched"
        assert matched[0].actual_request_rate_per_second == 35.0
        # residual = actual - predicted = 35.0 - 30.0 = 5.0
        assert math.isclose(matched[0].residual, 5.0)  # type: ignore[arg-type]
        assert math.isclose(matched[0].absolute_error, 5.0)  # type: ignore[arg-type]

    def test_signed_residual_negative(self) -> None:
        store = ForecastStore()
        gen_time = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        target_time = gen_time + timedelta(seconds=15)
        pf = PendingForecast(
            forecast_id="fc-neg",
            generated_at=gen_time,
            target_timestamp=target_time,
            horizon_seconds=15.0,
            model_name="persistence",
            predicted_request_rate_per_second=50.0,
            history_count=5,
        )
        store.add(pf)

        # Actual is 40.0 (over-predicted)
        matched = store.match_actuals(
            current_time=target_time,
            observation_timestamps=[target_time],
            observation_values=[40.0],
        )
        assert len(matched) == 1
        # residual = actual - predicted = 40.0 - 50.0 = -10.0
        assert math.isclose(matched[0].residual, -10.0)  # type: ignore[arg-type]
        # absolute_error = 10.0
        assert math.isclose(matched[0].absolute_error, 10.0)  # type: ignore[arg-type]

    def test_expires_unmatched_forecasts(self) -> None:
        store = ForecastStore()
        gen_time = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        target_time = gen_time + timedelta(seconds=15)
        pf = PendingForecast(
            forecast_id="fc-expire",
            generated_at=gen_time,
            target_timestamp=target_time,
            horizon_seconds=15.0,
            model_name="persistence",
            predicted_request_rate_per_second=20.0,
            history_count=5,
        )
        store.add(pf)

        # Fast forward 400 seconds later (> 300s max retention)
        late_time = target_time + timedelta(seconds=400)
        expired = store.expire_unmatched(late_time, max_retention_seconds=300.0)

        assert len(expired) == 1
        assert expired[0].status == "expired"
        assert len(store.get_pending()) == 0
        assert len(store.get_expired()) == 1
