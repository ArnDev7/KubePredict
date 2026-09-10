"""Forecasting evaluation metrics module (MAE, RMSE, sMAPE, Bias)."""

import math
from collections.abc import Sequence

import numpy as np
from pydantic import BaseModel, Field


class ForecastMetrics(BaseModel):
    """Container for forecast accuracy metrics."""

    mae: float = Field(ge=0.0)
    rmse: float = Field(ge=0.0)
    smape: float = Field(ge=0.0, le=200.0)
    bias: float
    valid_count: int = Field(ge=0)
    excluded_count: int = Field(default=0, ge=0)


def _validate_metric_inputs(
    actual: Sequence[float] | np.ndarray, predicted: Sequence[float] | np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Validate and convert actual and predicted sequences."""
    y = np.asarray(actual, dtype=np.float64)
    y_hat = np.asarray(predicted, dtype=np.float64)

    if y.ndim != 1 or y_hat.ndim != 1:
        raise ValueError("Inputs must be 1-dimensional sequences")
    if y.size != y_hat.size:
        raise ValueError(
            f"Length mismatch: actual has {y.size} items, predicted has {y_hat.size} items"
        )
    if y.size == 0:
        raise ValueError("Cannot calculate metrics on empty sequences")
    if not np.all(np.isfinite(y)):
        raise ValueError("Actual contains non-finite values (NaN or Inf)")
    if not np.all(np.isfinite(y_hat)):
        raise ValueError("Predicted contains non-finite values (NaN or Inf)")

    return y, y_hat


def calculate_mae(
    actual: Sequence[float] | np.ndarray, predicted: Sequence[float] | np.ndarray
) -> float:
    """Calculate Mean Absolute Error (MAE).

    MAE = (1 / n) * sum(|actual - predicted|)
    """
    y, y_hat = _validate_metric_inputs(actual, predicted)
    return float(np.mean(np.abs(y - y_hat)))


def calculate_rmse(
    actual: Sequence[float] | np.ndarray, predicted: Sequence[float] | np.ndarray
) -> float:
    """Calculate Root Mean Squared Error (RMSE).

    RMSE = sqrt((1 / n) * sum((actual - predicted)^2))
    """
    y, y_hat = _validate_metric_inputs(actual, predicted)
    return float(math.sqrt(float(np.mean((y - y_hat) ** 2))))


def calculate_smape(
    actual: Sequence[float] | np.ndarray, predicted: Sequence[float] | np.ndarray
) -> float:
    """Calculate Symmetric Mean Absolute Percentage Error (sMAPE) in percentage [0, 200]%.

    Formula:
        sMAPE = (100% / n) * sum( 2 * |actual - predicted| / (|actual| + |predicted|) )

    Zero-handling policy:
        When both actual and predicted are 0, the term is defined as 0.0.
        Division by zero is strictly avoided.
    """
    y, y_hat = _validate_metric_inputs(actual, predicted)
    diff = np.abs(y - y_hat)
    denom = np.abs(y) + np.abs(y_hat)

    # Where both are zero, term is 0.0
    both_zero = denom == 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        terms = np.where(both_zero, 0.0, (2.0 * diff) / denom)

    return float(np.mean(terms) * 100.0)


def calculate_bias(
    actual: Sequence[float] | np.ndarray, predicted: Sequence[float] | np.ndarray
) -> float:
    """Calculate forecast Bias (mean error).

    Bias = (1 / n) * sum(predicted - actual)

    A positive bias indicates systematic over-prediction; negative indicates under-prediction.
    """
    y, y_hat = _validate_metric_inputs(actual, predicted)
    return float(np.mean(y_hat - y))


def calculate_metrics(
    actual: Sequence[float], predicted: Sequence[float], excluded_count: int = 0
) -> ForecastMetrics:
    """Calculate all standard evaluation metrics for a set of predictions."""
    y, y_hat = _validate_metric_inputs(actual, predicted)
    return ForecastMetrics(
        mae=calculate_mae(y, y_hat),
        rmse=calculate_rmse(y, y_hat),
        smape=calculate_smape(y, y_hat),
        bias=calculate_bias(y, y_hat),
        valid_count=len(y),
        excluded_count=excluded_count,
    )
