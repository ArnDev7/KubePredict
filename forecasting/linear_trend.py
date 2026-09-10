"""Trailing linear-trend forecaster using ordinary least squares over time index."""

from typing import Any

import numpy as np

from forecasting.base import BaseForecaster


class LinearTrendForecaster(BaseForecaster):
    """Lightweight trend-aware forecaster using trailing linear regression over time index.

    Fitting Policy:
        Takes the trailing window of `window` observations y_0, ..., y_{w-1}.
        Assigns time index x = 0, ..., w-1.
        Fits slope m and intercept b via closed-form ordinary least squares:
            m = sum((x - x_bar) * (y - y_bar)) / sum((x - x_bar)^2)
            b = y_bar - m * x_bar
    Prediction Policy:
        For horizon step h in 1 ... H:
            x_future = (w - 1) + h
            y_pred = m * x_future + b
        Non-negative clipping is enforced by BaseForecaster.predict().
    """

    def __init__(self, window: int) -> None:
        super().__init__()
        if window < 2:
            raise ValueError(f"window must be at least 2 to fit a linear trend, got {window}")
        self._window = int(window)
        self._slope: float | None = None
        self._intercept: float | None = None

    @property
    def name(self) -> str:
        return "linear_trend"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"window": self._window}

    @property
    def window(self) -> int:
        return self._window

    @property
    def slope(self) -> float | None:
        return self._slope

    @property
    def intercept(self) -> float | None:
        return self._intercept

    @property
    def minimum_history_required(self) -> int:
        return self._window

    def _fit_internal(self, history: np.ndarray) -> None:
        y = history[-self._window :]
        w = self._window
        x = np.arange(w, dtype=np.float64)

        x_bar = (w - 1.0) / 2.0
        y_bar = float(np.mean(y))

        dx = x - x_bar
        dy = y - y_bar

        denom = float(np.sum(dx * dx))
        if denom == 0.0:
            # Degenerate case (should not happen for w >= 2)
            self._slope = 0.0
            self._intercept = y_bar
        else:
            self._slope = float(np.sum(dx * dy) / denom)
            self._intercept = float(y_bar - self._slope * x_bar)

    def _predict_internal(self, horizon_steps: int) -> list[float]:
        if self._slope is None or self._intercept is None:
            raise RuntimeError("Model has not been fitted")

        w = self._window
        preds: list[float] = []
        for h in range(1, horizon_steps + 1):
            x_future = (w - 1) + h
            pred_val = self._slope * x_future + self._intercept
            preds.append(float(pred_val))
        return preds
