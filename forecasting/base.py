"""Base classes and configuration contracts for offline forecasting."""

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, Field, field_validator, model_validator

from shared.validation import require_timezone_aware


class ForecastingConfig(BaseModel):
    """Type-safe forecasting evaluation configuration contract."""

    target_column: str = "request_rate_per_second"
    timestamp_column: str = "timestamp_utc"
    scenario_column: str = "scenario_name"
    sampling_interval_seconds: float = Field(default=15.0, gt=0.0)
    forecast_horizon_steps: list[int] = Field(default_factory=lambda: [1, 2, 4])
    minimum_training_samples: int = Field(default=6, gt=0)
    validation_fraction: float = Field(default=0.20, gt=0.0, lt=1.0)
    test_fraction: float = Field(default=0.20, gt=0.0, lt=1.0)
    rolling_step_size: int = Field(default=1, gt=0)
    moving_average_windows: list[int] = Field(default_factory=lambda: [3, 5, 8])
    ewma_alphas: list[float] = Field(default_factory=lambda: [0.2, 0.4, 0.6, 0.8])
    linear_trend_windows: list[int] = Field(default_factory=lambda: [3, 5, 8])
    primary_selection_metric: str = "mae"
    secondary_selection_metrics: list[str] = Field(default_factory=lambda: ["rmse", "smape"])
    minimum_required_scenarios: int = Field(default=4, ge=1)
    random_seed: int = 42
    missing_value_policy: str = "exclude_origin"
    output_root: str = "data/processed/forecasting"
    summary_output_root: str = "results/summaries/forecasting"

    model_config = {"extra": "forbid"}

    @field_validator("forecast_horizon_steps")
    @classmethod
    def validate_horizons(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("forecast_horizon_steps must not be empty")
        for h in v:
            if h <= 0:
                raise ValueError(f"horizon steps must be positive, got {h}")
        return sorted(v)

    @field_validator("moving_average_windows", "linear_trend_windows")
    @classmethod
    def validate_windows(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("windows list must not be empty")
        for w in v:
            if w <= 0:
                raise ValueError(f"window must be positive integer, got {w}")
        return sorted(v)

    @field_validator("ewma_alphas")
    @classmethod
    def validate_alphas(cls, v: list[float]) -> list[float]:
        if not v:
            raise ValueError("ewma_alphas must not be empty")
        for a in v:
            if not (0.0 < a <= 1.0):
                raise ValueError(f"ewma alpha must be in (0, 1], got {a}")
        return sorted(v)

    @field_validator("primary_selection_metric")
    @classmethod
    def validate_primary_metric(cls, v: str) -> str:
        metric = v.lower().strip()
        if metric not in {"mae", "rmse", "smape"}:
            raise ValueError(f"Unsupported primary metric: {v}")
        return metric

    @field_validator("output_root", "summary_output_root")
    @classmethod
    def validate_output_root(cls, v: str) -> str:
        norm = Path(v).as_posix()
        if not (
            norm.startswith(("data/processed", "results"))
            or "data/processed" in norm
            or "results" in norm
        ):
            raise ValueError(f"output root must reside inside data/processed or results, got: {v}")
        return norm

    @model_validator(mode="after")
    def validate_splits(self) -> "ForecastingConfig":
        if self.validation_fraction + self.test_fraction >= 1.0:
            raise ValueError(
                f"validation_fraction ({self.validation_fraction}) + "
                f"test_fraction ({self.test_fraction}) must be < 1.0"
            )
        return self


class BaseForecaster(ABC):
    """Abstract model-independent forecasting interface."""

    def __init__(self, **kwargs: Any) -> None:
        self._fitted = False

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the forecasting model family."""

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """Serializable parameters dictionary."""

    @property
    @abstractmethod
    def minimum_history_required(self) -> int:
        """Minimum number of observations required to produce a valid forecast."""

    def fit(self, history: list[float] | np.ndarray) -> "BaseForecaster":
        """Fit the model to chronological historical observations."""
        arr = np.asarray(history, dtype=np.float64)
        if arr.ndim != 1:
            raise ValueError("History must be a 1-dimensional sequence")
        if arr.size == 0:
            raise ValueError("History cannot be empty")
        if arr.size < self.minimum_history_required:
            raise ValueError(
                f"History length {arr.size} < minimum required {self.minimum_history_required}"
            )
        if not np.all(np.isfinite(arr)):
            raise ValueError("History contains non-finite (NaN or Inf) values")
        if np.any(arr < 0.0):
            raise ValueError("History contains negative values")

        self._fit_internal(arr.copy())
        self._fitted = True
        return self

    @abstractmethod
    def _fit_internal(self, history: np.ndarray) -> None:
        """Model-specific internal fitting logic on validated history copy."""

    def predict(self, horizon_steps: int) -> list[float]:
        """Generate point forecasts for the requested horizon steps."""
        if not self._fitted:
            raise RuntimeError("Cannot predict before model is fitted")
        if horizon_steps <= 0:
            raise ValueError(f"horizon_steps must be positive integer, got {horizon_steps}")

        raw_preds = self._predict_internal(horizon_steps)
        if len(raw_preds) != horizon_steps:
            raise ValueError(
                f"Prediction length {len(raw_preds)} does not match requested horizon {horizon_steps}"
            )
        if not all(np.isfinite(p) for p in raw_preds):
            raise ValueError("Model produced non-finite predictions")

        # Shared documented non-negative policy:
        # Request rates cannot physically be negative, so negative outputs are clipped to 0.0.
        return [max(0.0, float(p)) for p in raw_preds]

    @abstractmethod
    def _predict_internal(self, horizon_steps: int) -> list[float]:
        """Model-specific prediction logic producing horizon_steps point forecasts."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize forecaster specification."""
        return {
            "name": self.name,
            "parameters": self.parameters,
            "minimum_history_required": self.minimum_history_required,
        }


class ForecastInput(BaseModel):
    """Runtime input contract for pure in-process forecasting service."""

    generated_at: datetime
    sampling_interval_seconds: float = Field(gt=0.0)
    observations: list[float]
    observation_timestamps: list[datetime]
    requested_horizon_steps: int = Field(gt=0)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_contract(self) -> "ForecastInput":
        self.generated_at = require_timezone_aware(self.generated_at)
        if len(self.observations) != len(self.observation_timestamps):
            raise ValueError(
                f"Length of observations ({len(self.observations)}) must match "
                f"length of observation_timestamps ({len(self.observation_timestamps)})"
            )
        if len(self.observations) == 0:
            raise ValueError("observations must not be empty")

        for i, ts in enumerate(self.observation_timestamps):
            require_timezone_aware(ts)
            if i > 0 and ts <= self.observation_timestamps[i - 1]:
                raise ValueError(
                    f"observation_timestamps must be strictly chronological: {ts} <= {self.observation_timestamps[i - 1]}"
                )

        for val in self.observations:
            if not np.isfinite(val):
                raise ValueError(f"Observation value must be finite, got {val}")
            if val < 0.0:
                raise ValueError(f"Observation value must be non-negative, got {val}")

        return self


class ForecastOutput(BaseModel):
    """Runtime output contract for pure in-process forecasting service."""

    generated_at: datetime
    model_name: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    horizon_steps: int = Field(gt=0)
    horizon_seconds: float = Field(gt=0.0)
    predicted_values: list[float]
    predicted_request_rate_per_second: float = Field(ge=0.0)
    confidence_inputs: dict[str, Any] = Field(default_factory=dict)
    history_count: int = Field(gt=0)
    fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_output(self) -> "ForecastOutput":
        self.generated_at = require_timezone_aware(self.generated_at)
        if len(self.predicted_values) != self.horizon_steps:
            raise ValueError(
                f"predicted_values length ({len(self.predicted_values)}) must match "
                f"horizon_steps ({self.horizon_steps})"
            )
        for val in self.predicted_values:
            if not np.isfinite(val):
                raise ValueError(f"Predicted value must be finite, got {val}")
            if val < 0.0:
                raise ValueError(f"Predicted value must be non-negative, got {val}")

        # The final scalar predicted_request_rate_per_second must match the horizon point forecast
        if not np.isclose(self.predicted_request_rate_per_second, self.predicted_values[-1]):
            raise ValueError(
                "predicted_request_rate_per_second must equal the final horizon step prediction in predicted_values"
            )
        return self
