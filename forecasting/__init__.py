"""KubePredict Offline Forecasting Package (Phase 8A)."""

from forecasting.base import (
    BaseForecaster,
    ForecastingConfig,
    ForecastInput,
    ForecastOutput,
)
from forecasting.ewma import EWMAForecaster
from forecasting.linear_trend import LinearTrendForecaster
from forecasting.moving_average import MovingAverageForecaster
from forecasting.persistence import PersistenceForecaster
from forecasting.service import ForecastingService

__all__ = [
    "BaseForecaster",
    "EWMAForecaster",
    "ForecastInput",
    "ForecastOutput",
    "ForecastingConfig",
    "ForecastingService",
    "LinearTrendForecaster",
    "MovingAverageForecaster",
    "PersistenceForecaster",
]
