"""Pure error metrics calculation for Forecast Trust Guard (Phase 10A).

Calculates MAE, RMSE, sMAPE, signed bias, and window metadata over matched
forecast residual windows. Fully deterministic, non-mutating, and rejects non-finite values.
"""

import math
from collections.abc import Sequence
from datetime import datetime

import numpy as np
from pydantic import BaseModel, Field

from controller.forecast_store import PendingForecast
from shared.validation import require_timezone_aware


class WindowErrorMetrics(BaseModel):
    """Calculated error metrics over a recent matched residual window."""

    mae: float = Field(ge=0.0)
    rmse: float = Field(ge=0.0)
    smape: float = Field(ge=0.0, le=200.0)
    bias: float
    latest_absolute_error: float = Field(ge=0.0)
    matched_count: int = Field(gt=0)
    latest_residual_timestamp: datetime
    residual_window_age_seconds: float = Field(ge=0.0)

    model_config = {"extra": "forbid"}


def validate_series_inputs(
    actual: Sequence[float], predicted: Sequence[float]
) -> tuple[np.ndarray, np.ndarray]:
    """Validate that actual and predicted series are equal-length, non-empty, and finite."""
    if len(actual) == 0:
        raise ValueError("Series cannot be empty")
    if len(actual) != len(predicted):
        raise ValueError(
            f"Length mismatch between actual ({len(actual)}) and predicted ({len(predicted)})"
        )

    y = np.asarray(actual, dtype=np.float64)
    y_hat = np.asarray(predicted, dtype=np.float64)

    if not np.all(np.isfinite(y)):
        raise ValueError("Actual values contain NaN or infinite values")
    if not np.all(np.isfinite(y_hat)):
        raise ValueError("Predicted values contain NaN or infinite values")

    return y, y_hat


def calculate_window_mae(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """Calculate Mean Absolute Error (MAE).

    MAE = (1 / n) * sum(|actual - predicted|)
    """
    y, y_hat = validate_series_inputs(actual, predicted)
    return float(np.mean(np.abs(y - y_hat)))


def calculate_window_rmse(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """Calculate Root Mean Squared Error (RMSE).

    RMSE = sqrt((1 / n) * sum((actual - predicted)^2))
    """
    y, y_hat = validate_series_inputs(actual, predicted)
    return float(math.sqrt(float(np.mean((y - y_hat) ** 2))))


def calculate_window_smape(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """Calculate Symmetric Mean Absolute Percentage Error (sMAPE) in percentage [0, 200]%.

    Formula:
        sMAPE = (100% / n) * sum( 2 * |actual - predicted| / (|actual| + |predicted|) )

    Zero-handling policy:
        When both actual and predicted are 0.0, the term is defined as 0.0.
        Division by zero is strictly avoided.
    """
    y, y_hat = validate_series_inputs(actual, predicted)
    diff = np.abs(y - y_hat)
    denom = np.abs(y) + np.abs(y_hat)

    both_zero = denom == 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        terms = np.where(both_zero, 0.0, (2.0 * diff) / denom)

    return float(np.mean(terms) * 100.0)


def calculate_window_bias(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """Calculate signed forecast Bias (mean error).

    Bias = (1 / n) * sum(predicted - actual)

    A positive bias indicates systematic over-prediction; negative indicates under-prediction.
    """
    y, y_hat = validate_series_inputs(actual, predicted)
    return float(np.mean(y_hat - y))


def calculate_trust_metrics(
    records: Sequence[PendingForecast],
    current_time: datetime,
) -> WindowErrorMetrics:
    """Calculate all standard trust metrics for eligible matched forecast records.

    Parameters
    ----------
    records : Sequence[PendingForecast]
        Chronologically sorted eligible matched forecast records. Must not be empty.
    current_time : datetime
        Current evaluation UTC timestamp.

    Returns
    -------
    WindowErrorMetrics
        Validated metric object.
    """
    curr_utc = require_timezone_aware(current_time)
    if not records:
        raise ValueError("Cannot calculate metrics on empty records list")

    actuals: list[float] = []
    predictions: list[float] = []

    for r in records:
        if r.status != "matched":
            raise ValueError(
                f"Record {r.forecast_id} has status '{r.status}'; error metrics require 'matched'"
            )
        if r.actual_request_rate_per_second is None:
            raise ValueError(f"Record {r.forecast_id} has None for actual_request_rate_per_second")
        if r.target_timestamp > curr_utc:
            raise ValueError(
                f"Record {r.forecast_id} target_timestamp ({r.target_timestamp}) is in the future relative to ({curr_utc})"
            )
        actuals.append(r.actual_request_rate_per_second)
        predictions.append(r.predicted_request_rate_per_second)

    # Deterministic calculation
    mae = calculate_window_mae(actuals, predictions)
    rmse = calculate_window_rmse(actuals, predictions)
    smape = calculate_window_smape(actuals, predictions)
    bias = calculate_window_bias(actuals, predictions)

    # Latest residual (by target timestamp)
    sorted_by_target = sorted(records, key=lambda r: (r.target_timestamp, r.forecast_id))
    latest_rec = sorted_by_target[-1]
    latest_ts = latest_rec.target_timestamp
    latest_abs_err = (
        latest_rec.absolute_error
        if latest_rec.absolute_error is not None
        else abs(
            (latest_rec.actual_request_rate_per_second or 0.0)
            - latest_rec.predicted_request_rate_per_second
        )
    )
    age_seconds = max(0.0, (curr_utc - latest_ts).total_seconds())

    return WindowErrorMetrics(
        mae=round(mae, 4),
        rmse=round(rmse, 4),
        smape=round(smape, 4),
        bias=round(bias, 4),
        latest_absolute_error=round(latest_abs_err, 4),
        matched_count=len(records),
        latest_residual_timestamp=latest_ts,
        residual_window_age_seconds=round(age_seconds, 2),
    )
