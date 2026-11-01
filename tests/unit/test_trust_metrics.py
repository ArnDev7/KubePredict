"""Unit tests for trust_metrics.py (Phase 10A)."""

import math
from datetime import UTC, datetime, timedelta

import pytest

from controller.forecast_store import PendingForecast
from controller.trust_metrics import (
    calculate_trust_metrics,
    calculate_window_bias,
    calculate_window_mae,
    calculate_window_rmse,
    calculate_window_smape,
    validate_series_inputs,
)


def make_matched_record(
    forecast_id: str,
    target_time: datetime,
    predicted: float,
    actual: float,
) -> PendingForecast:
    residual = round(actual - predicted, 4)
    abs_err = round(abs(residual), 4)
    return PendingForecast(
        forecast_id=forecast_id,
        generated_at=target_time - timedelta(seconds=15),
        target_timestamp=target_time,
        horizon_seconds=15.0,
        model_name="persistence",
        predicted_request_rate_per_second=predicted,
        actual_request_rate_per_second=actual,
        residual=residual,
        absolute_error=abs_err,
        status="matched",
        history_count=1,
    )


class TestTrustMetrics:
    """Validate deterministic trust error metrics calculation."""

    def test_known_mae(self) -> None:
        # actuals = [10, 20, 30], preds = [12, 18, 33] -> diffs = [2, 2, 3] -> mean = 7/3 = 2.3333...
        mae = calculate_window_mae([10.0, 20.0, 30.0], [12.0, 18.0, 33.0])
        assert mae == pytest.approx(7.0 / 3.0, rel=1e-5)

    def test_known_rmse(self) -> None:
        # diffs^2 = [4, 4, 9] = 17 -> mean = 17/3 -> sqrt(17/3)
        rmse = calculate_window_rmse([10.0, 20.0, 30.0], [12.0, 18.0, 33.0])
        assert rmse == pytest.approx(math.sqrt(17.0 / 3.0), rel=1e-5)

    def test_known_smape(self) -> None:
        # actual = 10, pred = 10 -> diff=0 -> 0.0%
        # actual = 10, pred = 30 -> 2 * 20 / 40 * 100% = 100.0%
        smape = calculate_window_smape([10.0, 10.0], [10.0, 30.0])
        assert smape == pytest.approx(50.0, rel=1e-5)

    def test_known_bias(self) -> None:
        # pred - actual = [2, -2, 3] -> sum=3 -> mean=1.0
        bias = calculate_window_bias([10.0, 20.0, 30.0], [12.0, 18.0, 33.0])
        assert bias == pytest.approx(1.0, rel=1e-5)

    def test_both_zero_traffic(self) -> None:
        # Both zero returns 0.0 sMAPE, avoiding div by zero
        smape = calculate_window_smape([0.0, 0.0], [0.0, 0.0])
        assert smape == 0.0
        mae = calculate_window_mae([0.0], [0.0])
        assert mae == 0.0

    def test_zero_actual_with_positive_prediction(self) -> None:
        # actual = 0.0, pred = 10.0 -> term = 2 * 10 / 10 * 100% = 200.0%
        smape = calculate_window_smape([0.0], [10.0])
        assert smape == pytest.approx(200.0, rel=1e-5)
        mae = calculate_window_mae([0.0], [10.0])
        assert mae == 10.0

    def test_positive_actual_with_zero_prediction(self) -> None:
        # actual = 10.0, pred = 0.0 -> term = 2 * 10 / 10 * 100% = 200.0%
        smape = calculate_window_smape([10.0], [0.0])
        assert smape == pytest.approx(200.0, rel=1e-5)
        mae = calculate_window_mae([10.0], [0.0])
        assert mae == 10.0

    def test_non_finite_rejection(self) -> None:
        with pytest.raises(ValueError, match="NaN or infinite"):
            validate_series_inputs([10.0, float("nan")], [10.0, 10.0])

        with pytest.raises(ValueError, match="NaN or infinite"):
            validate_series_inputs([10.0, 10.0], [float("inf"), 10.0])

    def test_empty_input_rejection(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            validate_series_inputs([], [])

        with pytest.raises(ValueError, match="empty"):
            calculate_trust_metrics([], datetime.now(UTC))

    def test_calculate_trust_metrics_deterministic_output(self) -> None:
        now = datetime.now(UTC)
        recs = [
            make_matched_record("f1", now - timedelta(seconds=30), 10.0, 10.5),
            make_matched_record("f2", now - timedelta(seconds=15), 10.0, 9.5),
        ]
        m1 = calculate_trust_metrics(recs, now)
        m2 = calculate_trust_metrics(recs, now)

        assert m1.mae == m2.mae == 0.5
        assert m1.rmse == m2.rmse == 0.5
        assert m1.bias == m2.bias == 0.0
        assert m1.matched_count == 2
        assert m1.residual_window_age_seconds == pytest.approx(15.0, abs=0.1)
