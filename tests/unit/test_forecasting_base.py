"""Unit tests for the BaseForecaster interface and ForecastingConfig."""

import numpy as np
import pytest

from forecasting.base import BaseForecaster, ForecastingConfig


class DummyForecaster(BaseForecaster):
    """Minimal concrete implementation for testing the base contract."""

    def __init__(self, constant_val: float = 10.0) -> None:
        super().__init__()
        self._val = constant_val

    @property
    def name(self) -> str:
        return "dummy"

    @property
    def parameters(self) -> dict[str, float]:
        return {"constant_val": self._val}

    @property
    def minimum_history_required(self) -> int:
        return 2

    def _fit_internal(self, history: np.ndarray) -> None:
        pass

    def _predict_internal(self, horizon_steps: int) -> list[float]:
        return [self._val] * horizon_steps


class NegativeDummyForecaster(DummyForecaster):
    """Dummy forecaster that produces negative predictions to test clipping policy."""

    def _predict_internal(self, horizon_steps: int) -> list[float]:
        return [-5.0] * horizon_steps


def test_forecasting_config_valid() -> None:
    """Verify default and valid configuration initialization."""
    cfg = ForecastingConfig()
    assert cfg.target_column == "request_rate_per_second"
    assert cfg.forecast_horizon_steps == [1, 2, 4]
    assert cfg.primary_selection_metric == "mae"
    assert cfg.validation_fraction == 0.20
    assert cfg.test_fraction == 0.20


def test_forecasting_config_invalid() -> None:
    """Verify validation errors for bad configuration values."""
    with pytest.raises(ValueError, match="must be < 1.0"):
        ForecastingConfig(validation_fraction=0.6, test_fraction=0.5)

    with pytest.raises(ValueError, match="horizon steps must be positive"):
        ForecastingConfig(forecast_horizon_steps=[0, 1])

    with pytest.raises(ValueError, match="alpha must be in"):
        ForecastingConfig(ewma_alphas=[-0.1, 0.5])

    with pytest.raises(ValueError, match="output root must reside"):
        ForecastingConfig(output_root="/tmp/evil")


def test_base_forecaster_predict_before_fit() -> None:
    """Verify predict raises RuntimeError before fitting."""
    model = DummyForecaster()
    with pytest.raises(RuntimeError, match="Cannot predict before model is fitted"):
        model.predict(1)


def test_base_forecaster_fit_empty() -> None:
    """Verify empty history is rejected."""
    model = DummyForecaster()
    with pytest.raises(ValueError, match="History cannot be empty"):
        model.fit([])


def test_base_forecaster_insufficient_history() -> None:
    """Verify error when history length is below minimum."""
    model = DummyForecaster()
    with pytest.raises(ValueError, match="History length 1 < minimum required 2"):
        model.fit([1.0])


def test_base_forecaster_non_finite_history() -> None:
    """Verify non-finite values in history are rejected."""
    model = DummyForecaster()
    with pytest.raises(ValueError, match="non-finite"):
        model.fit([1.0, float("nan")])
    with pytest.raises(ValueError, match="non-finite"):
        model.fit([1.0, float("inf")])


def test_base_forecaster_negative_history() -> None:
    """Verify negative history values are rejected."""
    model = DummyForecaster()
    with pytest.raises(ValueError, match="History contains negative values"):
        model.fit([1.0, -2.0])


def test_base_forecaster_input_non_mutation() -> None:
    """Verify fitting does not mutate caller's original sequence."""
    model = DummyForecaster()
    orig = [10.0, 20.0, 30.0]
    orig_copy = orig.copy()
    model.fit(orig)
    assert orig == orig_copy


def test_base_forecaster_deterministic_predictions() -> None:
    """Verify repeated predictions produce identical results."""
    model = DummyForecaster(42.0)
    model.fit([1.0, 2.0])
    p1 = model.predict(3)
    p2 = model.predict(3)
    assert p1 == p2 == [42.0, 42.0, 42.0]


def test_base_forecaster_output_length_and_horizon_validation() -> None:
    """Verify horizon validation and output length."""
    model = DummyForecaster()
    model.fit([1.0, 2.0])
    with pytest.raises(ValueError, match="horizon_steps must be positive"):
        model.predict(0)
    preds = model.predict(5)
    assert len(preds) == 5


def test_base_forecaster_non_negative_clipping_policy() -> None:
    """Verify shared non-negative clipping policy clips negative outputs to 0.0."""
    model = NegativeDummyForecaster()
    model.fit([1.0, 2.0])
    preds = model.predict(2)
    assert preds == [0.0, 0.0]


def test_base_forecaster_to_dict() -> None:
    """Verify serialization to dictionary."""
    model = DummyForecaster(7.5)
    d = model.to_dict()
    assert d["name"] == "dummy"
    assert d["parameters"] == {"constant_val": 7.5}
    assert d["minimum_history_required"] == 2
