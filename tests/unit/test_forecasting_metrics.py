"""Unit tests for forecasting evaluation metrics (MAE, RMSE, sMAPE, Bias)."""

import math

import pytest

from forecasting.metrics import (
    calculate_bias,
    calculate_mae,
    calculate_metrics,
    calculate_rmse,
    calculate_smape,
)


def test_mae_known_values() -> None:
    """Verify MAE on a known numerical sequence."""
    actual = [10.0, 20.0, 30.0]
    predicted = [12.0, 18.0, 35.0]
    # diffs = [2.0, 2.0, 5.0] -> sum = 9.0 -> mean = 3.0
    assert calculate_mae(actual, predicted) == pytest.approx(3.0)


def test_rmse_known_values() -> None:
    """Verify RMSE on a known numerical sequence."""
    actual = [10.0, 20.0]
    predicted = [13.0, 16.0]
    # diffs = [3.0, -4.0] -> sq = [9.0, 16.0] -> mean = 12.5 -> sqrt = 3.5355339
    assert calculate_rmse(actual, predicted) == pytest.approx(math.sqrt(12.5))


def test_smape_known_values() -> None:
    """Verify sMAPE formula on a known numerical example."""
    actual = [100.0]
    predicted = [110.0]
    # |100 - 110| = 10; 100 + 110 = 210 -> (2 * 10 / 210) * 100% = 9.5238095%
    expected = (20.0 / 210.0) * 100.0
    assert calculate_smape(actual, predicted) == pytest.approx(expected)


def test_smape_both_zero() -> None:
    """Verify sMAPE handles zero actual and zero prediction gracefully without division by zero."""
    actual = [0.0, 0.0]
    predicted = [0.0, 0.0]
    assert calculate_smape(actual, predicted) == pytest.approx(0.0)


def test_smape_one_zero() -> None:
    """Verify sMAPE when one is zero and other is positive equals 200%."""
    actual = [0.0]
    predicted = [10.0]
    # |0 - 10| = 10; 0 + 10 = 10 -> (2 * 10 / 10) * 100% = 200.0%
    assert calculate_smape(actual, predicted) == pytest.approx(200.0)


def test_bias_calculation() -> None:
    """Verify bias calculation: mean(predicted - actual)."""
    actual = [10.0, 20.0, 30.0]
    predicted = [12.0, 23.0, 34.0]
    # errors = [+2, +3, +4] -> mean = +3.0 (over-prediction)
    assert calculate_bias(actual, predicted) == pytest.approx(3.0)

    # Under-prediction
    under_pred = [8.0, 18.0, 28.0]
    assert calculate_bias(actual, under_pred) == pytest.approx(-2.0)


def test_metrics_length_mismatch() -> None:
    """Verify error on unequal input lengths."""
    with pytest.raises(ValueError, match="Length mismatch"):
        calculate_mae([1.0, 2.0], [1.0])


def test_metrics_empty_inputs() -> None:
    """Verify error on empty sequences."""
    with pytest.raises(ValueError, match="empty sequences"):
        calculate_rmse([], [])


def test_metrics_nan_rejection() -> None:
    """Verify NaN or Inf in actual or predicted is rejected."""
    with pytest.raises(ValueError, match="Actual contains non-finite values"):
        calculate_mae([float("nan"), 1.0], [1.0, 1.0])
    with pytest.raises(ValueError, match="Predicted contains non-finite values"):
        calculate_mae([1.0, 1.0], [1.0, float("inf")])


def test_calculate_metrics_container() -> None:
    """Verify calculate_metrics returns a valid ForecastMetrics container."""
    res = calculate_metrics([10.0, 20.0], [12.0, 18.0], excluded_count=3)
    assert res.valid_count == 2
    assert res.excluded_count == 3
    assert res.mae == pytest.approx(2.0)
    assert res.bias == pytest.approx(0.0)
