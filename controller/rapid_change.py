"""Deterministic rapid-change detection for KubePredict controller (Phase 10B).

Evaluates whether recent request-rate telemetry exhibits an abrupt upward surge
meeting both relative growth ratio and absolute increase thresholds.
Zero statistical drift detectors, zero machine learning, and zero external mutations.
"""

import math
from datetime import UTC, datetime

from pydantic import BaseModel, Field, model_validator

from controller.policy_config import PolicyConfig
from controller.telemetry import TelemetryResult
from shared.enums import DecisionReason
from shared.validation import require_timezone_aware

# Epsilon for near-zero baseline division protection (documented and non-zero)
BASELINE_EPSILON: float = 1e-4


class RapidChangeResult(BaseModel):
    """Structured result of pure deterministic rapid-change evaluation."""

    evaluated_at: datetime
    detected: bool
    recent_rate: float | None = None
    baseline_rate: float | None = None
    absolute_increase: float | None = None
    growth_ratio: float | None = None
    sample_count: int = Field(ge=0)
    window_start: datetime | None = None
    window_end: datetime | None = None
    thresholds: dict[str, float] = Field(default_factory=dict)
    reason: DecisionReason
    warnings: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_rapid_change_result(self) -> "RapidChangeResult":
        self.evaluated_at = require_timezone_aware(self.evaluated_at)
        if self.window_start is not None:
            self.window_start = require_timezone_aware(self.window_start)
        if self.window_end is not None:
            self.window_end = require_timezone_aware(self.window_end)
        return self


def detect_rapid_change(
    telemetry: TelemetryResult | None,
    config: PolicyConfig,
    evaluated_at: datetime | None = None,
) -> RapidChangeResult:
    """Evaluate deterministic rapid change in request rate from telemetry.

    Parameters
    ----------
    telemetry : TelemetryResult | None
        Recent telemetry query result.
    config : PolicyConfig
        Validated policy configuration containing thresholds and window sizes.
    evaluated_at : datetime | None
        Evaluation timestamp (defaults to UTC now).

    Returns
    -------
    RapidChangeResult
        Typed evaluation outcome.
    """
    eval_time = require_timezone_aware(evaluated_at or datetime.now(UTC))

    thresholds: dict[str, float] = {
        "rapid_change_growth_ratio": config.rapid_change_growth_ratio,
        "rapid_change_absolute_rate": config.rapid_change_absolute_rate,
        "request_rate_window_samples": float(config.request_rate_window_samples),
        "minimum_valid_samples": float(config.minimum_valid_samples),
    }

    # 1. Telemetry availability check
    if telemetry is None or not telemetry.timestamps or not telemetry.values:
        return RapidChangeResult(
            evaluated_at=eval_time,
            detected=False,
            sample_count=0,
            thresholds=thresholds,
            reason=DecisionReason.TELEMETRY_UNAVAILABLE,
            warnings=["Telemetry result is None or contains zero observations"],
        )

    # 2. Telemetry freshness check
    if not telemetry.is_fresh:
        return RapidChangeResult(
            evaluated_at=eval_time,
            detected=False,
            sample_count=len(telemetry.values),
            thresholds=thresholds,
            reason=DecisionReason.TELEMETRY_STALE,
            warnings=[f"Telemetry is not fresh (age={telemetry.latest_age_seconds:.1f}s)"],
        )

    # 3. Defensive copy to prevent caller mutation
    timestamps = list(telemetry.timestamps)
    values = list(telemetry.values)

    # 4. Check for non-finite values
    for val in values:
        if not math.isfinite(val) or val < 0.0:
            return RapidChangeResult(
                evaluated_at=eval_time,
                detected=False,
                sample_count=len(values),
                thresholds=thresholds,
                reason=DecisionReason.INSUFFICIENT_HISTORY,
                warnings=["Telemetry contains non-finite or negative request rate values"],
            )

    # 5. Check chronological ordering
    for i in range(1, len(timestamps)):
        if timestamps[i] <= timestamps[i - 1]:
            return RapidChangeResult(
                evaluated_at=eval_time,
                detected=False,
                sample_count=len(values),
                thresholds=thresholds,
                reason=DecisionReason.INSUFFICIENT_HISTORY,
                warnings=["Telemetry timestamps are not strictly chronological"],
            )

    # 6. Extract sliding window of request_rate_window_samples
    window_samples = config.request_rate_window_samples
    if len(values) < config.minimum_valid_samples:
        return RapidChangeResult(
            evaluated_at=eval_time,
            detected=False,
            sample_count=len(values),
            thresholds=thresholds,
            reason=DecisionReason.INSUFFICIENT_HISTORY,
            warnings=[
                f"Observation count ({len(values)}) < minimum_valid_samples ({config.minimum_valid_samples})"
            ],
        )

    window_timestamps = timestamps[-window_samples:]
    window_values = values[-window_samples:]
    sample_count = len(window_values)

    recent_rate = float(window_values[-1])
    baseline_samples = window_values[:-1]
    baseline_rate = float(sum(baseline_samples) / len(baseline_samples))

    absolute_increase = recent_rate - baseline_rate
    effective_baseline_for_ratio = max(baseline_rate, BASELINE_EPSILON)
    growth_ratio = recent_rate / effective_baseline_for_ratio

    # 7. Rapid change evaluation criteria:
    # Requires BOTH absolute increase >= threshold AND growth ratio >= threshold
    detected = (
        absolute_increase >= config.rapid_change_absolute_rate
        and growth_ratio >= config.rapid_change_growth_ratio
    )

    reason = DecisionReason.RAPID_CHANGE_DETECTED if detected else DecisionReason.NO_CHANGE_REQUIRED

    return RapidChangeResult(
        evaluated_at=eval_time,
        detected=detected,
        recent_rate=recent_rate,
        baseline_rate=baseline_rate,
        absolute_increase=absolute_increase,
        growth_ratio=growth_ratio,
        sample_count=sample_count,
        window_start=window_timestamps[0],
        window_end=window_timestamps[-1],
        thresholds=thresholds,
        reason=reason,
        warnings=[],
    )
