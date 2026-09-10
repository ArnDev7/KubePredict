"""Unit tests for the four forecasting model families (Persistence, MA, EWMA, LinearTrend)."""

import pytest

from forecasting.ewma import EWMAForecaster
from forecasting.linear_trend import LinearTrendForecaster
from forecasting.moving_average import MovingAverageForecaster
from forecasting.persistence import PersistenceForecaster

# ==============================================================================
# 1. Persistence Forecaster Tests
# ==============================================================================


def test_persistence_single_observation() -> None:
    """Verify persistence fits on a single observation."""
    model = PersistenceForecaster()
    assert model.minimum_history_required == 1
    model.fit([12.5])
    preds = model.predict(3)
    assert preds == [12.5, 12.5, 12.5]


def test_persistence_multiple_observations() -> None:
    """Verify persistence predicts the latest observation."""
    model = PersistenceForecaster()
    model.fit([1.0, 5.0, 10.0, 42.0])
    assert model.predict(2) == [42.0, 42.0]


def test_persistence_zero_value() -> None:
    """Verify persistence correctly handles a latest value of zero."""
    model = PersistenceForecaster()
    model.fit([10.0, 0.0])
    assert model.predict(4) == [0.0, 0.0, 0.0, 0.0]


def test_persistence_parameters_and_name() -> None:
    """Verify model name and empty parameter set."""
    model = PersistenceForecaster()
    assert model.name == "persistence"
    assert model.parameters == {}


# ==============================================================================
# 2. Moving Average Forecaster Tests
# ==============================================================================


def test_moving_average_window_validation() -> None:
    """Verify window must be positive."""
    with pytest.raises(ValueError, match="window must be a positive integer"):
        MovingAverageForecaster(window=0)
    with pytest.raises(ValueError, match="window must be a positive integer"):
        MovingAverageForecaster(window=-3)


def test_moving_average_calculation() -> None:
    """Verify trailing-only mean across window."""
    model = MovingAverageForecaster(window=3)
    assert model.minimum_history_required == 3
    # History has 5 elements; only last 3 [10, 20, 30] should be used -> mean = 20.0
    model.fit([1.0, 2.0, 10.0, 20.0, 30.0])
    preds = model.predict(2)
    assert preds == [20.0, 20.0]


def test_moving_average_insufficient_history() -> None:
    """Verify error if history has fewer samples than window."""
    model = MovingAverageForecaster(window=5)
    with pytest.raises(ValueError, match="History length 4 < minimum required 5"):
        model.fit([1.0, 2.0, 3.0, 4.0])


def test_moving_average_trailing_only_no_leakage() -> None:
    """Verify no centered or future leakage occurs."""
    model = MovingAverageForecaster(window=2)
    model.fit([100.0, 2.0, 4.0])  # last 2 are 2.0 and 4.0 -> mean 3.0
    assert model.predict(1) == [3.0]


# ==============================================================================
# 3. EWMA Forecaster Tests
# ==============================================================================


def test_ewma_alpha_validation() -> None:
    """Verify alpha must be in (0, 1]."""
    with pytest.raises(ValueError, match="alpha must be in"):
        EWMAForecaster(alpha=0.0)
    with pytest.raises(ValueError, match="alpha must be in"):
        EWMAForecaster(alpha=1.5)
    with pytest.raises(ValueError, match="alpha must be in"):
        EWMAForecaster(alpha=-0.2)


def test_ewma_constant_series() -> None:
    """Verify constant series yields the constant value."""
    model = EWMAForecaster(alpha=0.4)
    model.fit([10.0, 10.0, 10.0, 10.0])
    assert model.predict(3) == [10.0, 10.0, 10.0]


def test_ewma_recurrence_calculation() -> None:
    """Verify step-by-step recurrence with known numerical values.

    s_0 = 10.0
    alpha = 0.5
    y_1 = 20.0 -> s_1 = 0.5 * 20 + 0.5 * 10 = 15.0
    y_2 = 30.0 -> s_2 = 0.5 * 30 + 0.5 * 15 = 22.5
    """
    model = EWMAForecaster(alpha=0.5)
    model.fit([10.0, 20.0, 30.0])
    preds = model.predict(2)
    assert preds == [22.5, 22.5]


def test_ewma_alpha_one_equals_persistence() -> None:
    """Verify alpha = 1.0 reduces to persistence."""
    ewma_one = EWMAForecaster(alpha=1.0)
    persist = PersistenceForecaster()
    history = [5.0, 12.0, 7.0, 99.0]
    ewma_one.fit(history)
    persist.fit(history)
    assert ewma_one.predict(4) == persist.predict(4) == [99.0, 99.0, 99.0, 99.0]


# ==============================================================================
# 4. Linear Trend Forecaster Tests
# ==============================================================================


def test_linear_trend_window_validation() -> None:
    """Verify linear trend requires window >= 2."""
    with pytest.raises(ValueError, match="window must be at least 2"):
        LinearTrendForecaster(window=1)


def test_linear_trend_constant_series() -> None:
    """Verify constant series has zero slope and flat forecast."""
    model = LinearTrendForecaster(window=3)
    model.fit([5.0, 5.0, 5.0])
    assert model.slope == pytest.approx(0.0)
    assert model.intercept == pytest.approx(5.0)
    assert model.predict(3) == pytest.approx([5.0, 5.0, 5.0])


def test_linear_trend_increasing_series() -> None:
    """Verify linear trend extrapolates an increasing series.

    history = [10.0, 20.0, 30.0], window = 3
    x = [0, 1, 2], y = [10, 20, 30]
    slope = 10.0, intercept = 10.0
    for h=1 (x=3): pred = 10 * 3 + 10 = 40.0
    for h=2 (x=4): pred = 10 * 4 + 10 = 50.0
    """
    model = LinearTrendForecaster(window=3)
    model.fit([10.0, 20.0, 30.0])
    assert model.slope == pytest.approx(10.0)
    assert model.intercept == pytest.approx(10.0)
    assert model.predict(2) == pytest.approx([40.0, 50.0])


def test_linear_trend_decreasing_with_non_negative_clipping() -> None:
    """Verify linear trend with steep negative slope clips to 0.0."""
    model = LinearTrendForecaster(window=3)
    # y = [30, 20, 10] -> slope = -10, intercept = 30
    # h=1 (x=3): pred = -10 * 3 + 30 = 0.0
    # h=2 (x=4): pred = -10 * 4 + 30 = -10.0 -> clipped to 0.0
    model.fit([30.0, 20.0, 10.0])
    preds = model.predict(2)
    assert preds == [0.0, 0.0]


def test_linear_trend_trailing_window_only() -> None:
    """Verify only the trailing window is used for fitting."""
    model = LinearTrendForecaster(window=2)
    # Full history: [100.0, 10.0, 20.0]
    # Trailing window of 2 is [10.0, 20.0] -> x=[0, 1], y=[10, 20] -> slope=10, intercept=10
    model.fit([100.0, 10.0, 20.0])
    assert model.slope == pytest.approx(10.0)
    assert model.predict(1) == pytest.approx([30.0])
