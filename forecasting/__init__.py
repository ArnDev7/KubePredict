"""KubePredict Offline Forecasting Package (Phase 8A)."""

from forecasting.base import BaseForecaster, ForecastingConfig
from forecasting.ewma import EWMAForecaster
from forecasting.linear_trend import LinearTrendForecaster
from forecasting.moving_average import MovingAverageForecaster
from forecasting.persistence import PersistenceForecaster

__all__ = [
    "BaseForecaster",
    "EWMAForecaster",
    "ForecastingConfig",
    "LinearTrendForecaster",
    "MovingAverageForecaster",
    "PersistenceForecaster",
]
