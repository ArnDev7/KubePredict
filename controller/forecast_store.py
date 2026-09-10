"""Pending forecast store and delayed actual-outcome matching (Phase 9A)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from shared.validation import require_timezone_aware

ForecastStatus = Literal["pending", "matched", "expired"]


class PendingForecast(BaseModel):
    """Record of a generated point forecast awaiting actual telemetry."""

    forecast_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    generated_at: datetime
    target_timestamp: datetime
    horizon_seconds: float = Field(gt=0.0)
    model_name: str = Field(min_length=1)
    predicted_request_rate_per_second: float = Field(ge=0.0)
    fallback_used: bool = False
    history_count: int = Field(gt=0)
    warnings: list[str] = Field(default_factory=list)
    actual_request_rate_per_second: float | None = Field(default=None, ge=0.0)
    residual: float | None = None  # actual - predicted
    absolute_error: float | None = Field(default=None, ge=0.0)  # |actual - predicted|
    status: ForecastStatus = "pending"

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_timestamps(self) -> "PendingForecast":
        self.generated_at = require_timezone_aware(self.generated_at)
        self.target_timestamp = require_timezone_aware(self.target_timestamp)
        if self.target_timestamp <= self.generated_at:
            raise ValueError(
                f"target_timestamp ({self.target_timestamp}) must be strictly after "
                f"generated_at ({self.generated_at})"
            )
        return self


class ForecastStore:
    """In-memory store managing pending forecast lifecycle and residual matching."""

    def __init__(self) -> None:
        self._records: dict[str, PendingForecast] = {}

    def add(self, forecast: PendingForecast) -> None:
        """Register a new forecast record."""
        if forecast.forecast_id in self._records:
            raise ValueError(f"Duplicate forecast ID: {forecast.forecast_id}")
        self._records[forecast.forecast_id] = forecast

    def match_actuals(
        self,
        current_time: datetime,
        observation_timestamps: list[datetime],
        observation_values: list[float],
        tolerance_seconds: float = 15.0,
    ) -> list[PendingForecast]:
        """Match pending forecasts whose target timestamp has arrived against observations.

        Parameters
        ----------
        current_time : datetime
            Current UTC clock time. Forecasts whose target_timestamp > current_time
            are NOT matched yet (preventing premature / future matching).
        observation_timestamps : list[datetime]
            Available chronological observation timestamps.
        observation_values : list[float]
            Corresponding request rate observation values.
        tolerance_seconds : float
            Maximum allowable delta between observation timestamp and target timestamp.

        Returns
        -------
        list[PendingForecast]
            Forecasts matched during this invocation.
        """
        curr_utc = require_timezone_aware(current_time)
        newly_matched: list[PendingForecast] = []

        if not observation_timestamps or not observation_values:
            return newly_matched

        obs_pairs = list(zip(observation_timestamps, observation_values, strict=True))

        for rec in self._records.values():
            if rec.status != "pending":
                continue

            # Target timestamp must have arrived
            if rec.target_timestamp > curr_utc:
                continue

            # Find best matching observation within tolerance
            best_diff = float("inf")
            best_val: float | None = None

            for obs_ts, obs_val in obs_pairs:
                diff_s = abs((obs_ts - rec.target_timestamp).total_seconds())
                if diff_s <= tolerance_seconds and diff_s < best_diff:
                    best_diff = diff_s
                    best_val = obs_val

            if best_val is not None:
                rec.actual_request_rate_per_second = best_val
                rec.residual = round(best_val - rec.predicted_request_rate_per_second, 4)
                rec.absolute_error = round(abs(rec.residual), 4)
                rec.status = "matched"
                newly_matched.append(rec)

        return newly_matched

    def expire_unmatched(
        self,
        current_time: datetime,
        max_retention_seconds: float = 300.0,
    ) -> list[PendingForecast]:
        """Expire pending forecasts whose target time has elapsed beyond the retention limit."""
        curr_utc = require_timezone_aware(current_time)
        newly_expired: list[PendingForecast] = []

        for rec in self._records.values():
            if rec.status != "pending":
                continue
            elapsed_s = (curr_utc - rec.target_timestamp).total_seconds()
            if elapsed_s > max_retention_seconds:
                rec.status = "expired"
                newly_expired.append(rec)

        return newly_expired

    def get_pending(self) -> list[PendingForecast]:
        """Retrieve all currently pending forecasts."""
        return [r for r in self._records.values() if r.status == "pending"]

    def get_matched(self) -> list[PendingForecast]:
        """Retrieve all successfully matched forecasts."""
        return [r for r in self._records.values() if r.status == "matched"]

    def get_expired(self) -> list[PendingForecast]:
        """Retrieve all expired forecasts."""
        return [r for r in self._records.values() if r.status == "expired"]

    def get_all(self) -> list[PendingForecast]:
        """Retrieve all forecast records in deterministic order."""
        return sorted(self._records.values(), key=lambda r: r.generated_at)

    def count(self) -> int:
        """Total records stored."""
        return len(self._records)
