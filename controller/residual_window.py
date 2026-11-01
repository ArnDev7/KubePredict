"""Residual window builder for Forecast Trust Guard (Phase 10A).

Filters matched forecast records by evidence window, eliminates future records,
and produces deterministic residual subsets without modifying source stores.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from controller.forecast_store import PendingForecast
from shared.validation import require_timezone_aware


class ResidualWindowResult(BaseModel):
    """Result of residual window construction."""

    eligible_records: list[PendingForecast] = Field(default_factory=list)
    excluded_too_old_count: int = Field(ge=0)
    excluded_future_count: int = Field(ge=0)
    excluded_invalid_count: int = Field(ge=0)
    window_start: datetime | None = None
    window_end: datetime | None = None
    latest_residual_timestamp: datetime | None = None
    latest_residual_age_seconds: float | None = Field(default=None, ge=0.0)
    evidence_sufficiency: bool

    model_config = {"extra": "forbid"}


def build_residual_window(
    matched_records: Sequence[PendingForecast],
    evaluation_time: datetime,
    evidence_window_seconds: float,
    residual_max_age_seconds: float,
    minimum_matched_residuals: int = 1,
) -> ResidualWindowResult:
    """Build a deterministic residual window from matched forecast records.

    Parameters
    ----------
    matched_records : Sequence[PendingForecast]
        Candidate forecast records. Caller data is not mutated.
    evaluation_time : datetime
        Current evaluation time (must be timezone-aware).
    evidence_window_seconds : float
        Length of the historical evidence window in seconds (must be > 0).
    residual_max_age_seconds : float
        Maximum allowable age for the most recent residual (must be > 0).
    minimum_matched_residuals : int
        Minimum number of eligible matched residuals for sufficiency.

    Returns
    -------
    ResidualWindowResult
        Window result containing eligible records, exclusions, and timestamps.
    """
    eval_utc = require_timezone_aware(evaluation_time)
    if evidence_window_seconds <= 0:
        raise ValueError("evidence_window_seconds must be > 0")
    if residual_max_age_seconds <= 0:
        raise ValueError("residual_max_age_seconds must be > 0")
    if minimum_matched_residuals <= 0:
        raise ValueError("minimum_matched_residuals must be > 0")

    window_start = eval_utc - timedelta(seconds=evidence_window_seconds)
    window_end = eval_utc

    seen_ids: set[str] = set()
    eligible: list[PendingForecast] = []
    excluded_too_old = 0
    excluded_future = 0
    excluded_invalid = 0

    for rec in matched_records:
        # Check duplicate forecast IDs
        if rec.forecast_id in seen_ids:
            raise ValueError(
                f"Duplicate forecast ID detected in candidate records: {rec.forecast_id}"
            )
        seen_ids.add(rec.forecast_id)

        # Exclude invalid or unmatched records
        if rec.status != "matched" or rec.actual_request_rate_per_second is None:
            excluded_invalid += 1
            continue

        # Target timestamp must not be after evaluation time
        if rec.target_timestamp > eval_utc:
            excluded_future += 1
            continue

        # Target timestamp must not be older than the evidence window
        if rec.target_timestamp < window_start:
            excluded_too_old += 1
            continue

        eligible.append(rec)

    # Sort deterministically by target timestamp, then forecast_id
    eligible.sort(key=lambda r: (r.target_timestamp, r.forecast_id))

    latest_ts: datetime | None = None
    latest_age: float | None = None
    if eligible:
        latest_ts = eligible[-1].target_timestamp
        latest_age = max(0.0, round((eval_utc - latest_ts).total_seconds(), 2))

    is_sufficient = (
        len(eligible) >= minimum_matched_residuals
        and latest_age is not None
        and latest_age <= residual_max_age_seconds
    )

    return ResidualWindowResult(
        eligible_records=eligible,
        excluded_too_old_count=excluded_too_old,
        excluded_future_count=excluded_future,
        excluded_invalid_count=excluded_invalid,
        window_start=window_start,
        window_end=window_end,
        latest_residual_timestamp=latest_ts,
        latest_residual_age_seconds=latest_age,
        evidence_sufficiency=is_sufficient,
    )
