"""Trailing moving-average baseline forecaster."""

from typing import Any

import numpy as np

from forecasting.base import BaseForecaster


class MovingAverageForecaster(BaseForecaster):
    """Trailing moving-average forecaster: mean of the latest window repeated across horizon."""

    def __init__(self, window: int) -> None:
        super().__init__()
        if window <= 0:
            raise ValueError(f"window must be a positive integer, got {window}")
        self._window = int(window)
        self._mean_value: float | None = None

    @property
    def name(self) -> str:
        return "moving_average"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"window": self._window}

    @property
    def window(self) -> int:
        return self._window

    @property
    def minimum_history_required(self) -> int:
        return self._window

    def _fit_internal(self, history: np.ndarray) -> None:
        trailing = history[-self._window :]
        self._mean_value = float(np.mean(trailing))

    def _predict_internal(self, horizon_steps: int) -> list[float]:
        if self._mean_value is None:
            raise RuntimeError("Model has not computed a mean value")
        return [self._mean_value] * horizon_steps
