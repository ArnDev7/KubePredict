"""Persistence (naive last-value) baseline forecaster."""

from typing import Any

import numpy as np

from forecasting.base import BaseForecaster


class PersistenceForecaster(BaseForecaster):
    """Naive persistence baseline: forecasts repeat the latest observed value."""

    def __init__(self) -> None:
        super().__init__()
        self._last_value: float | None = None

    @property
    def name(self) -> str:
        return "persistence"

    @property
    def parameters(self) -> dict[str, Any]:
        return {}

    @property
    def minimum_history_required(self) -> int:
        return 1

    def _fit_internal(self, history: np.ndarray) -> None:
        self._last_value = float(history[-1])

    def _predict_internal(self, horizon_steps: int) -> list[float]:
        if self._last_value is None:
            raise RuntimeError("Model has not recorded a last value")
        return [self._last_value] * horizon_steps
