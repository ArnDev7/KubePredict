"""Exponentially Weighted Moving Average (EWMA) baseline forecaster."""

from typing import Any

import numpy as np

from forecasting.base import BaseForecaster


class EWMAForecaster(BaseForecaster):
    """Exponentially weighted moving-average forecaster.

    Initialisation Policy:
        s_0 = history[0] (the first historical observation).
    Recursive Update:
        s_t = alpha * history[t] + (1 - alpha) * s_{t-1} for t = 1 ... k-1.
    Prediction Policy:
        The final recursive state s_{k-1} is repeated across all horizon steps.
    """

    def __init__(self, alpha: float) -> None:
        super().__init__()
        if not (0.0 < alpha <= 1.0):
            raise ValueError(f"alpha must be in (0, 1], got {alpha}")
        self._alpha = float(alpha)
        self._final_state: float | None = None

    @property
    def name(self) -> str:
        return "ewma"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"alpha": self._alpha}

    @property
    def alpha(self) -> float:
        return self._alpha

    @property
    def minimum_history_required(self) -> int:
        return 1

    def _fit_internal(self, history: np.ndarray) -> None:
        state = float(history[0])
        alpha = self._alpha
        one_minus_alpha = 1.0 - alpha
        for val in history[1:]:
            state = alpha * float(val) + one_minus_alpha * state
        self._final_state = state

    def _predict_internal(self, horizon_steps: int) -> list[float]:
        if self._final_state is None:
            raise RuntimeError("Model has not computed a final EWMA state")
        return [self._final_state] * horizon_steps
