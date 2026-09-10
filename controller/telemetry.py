"""Live Prometheus telemetry reader and TelemetryResult contract (Phase 9A)."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator

from controller.config import ObserverConfig
from experiments.telemetry.prometheus_client import PrometheusClient
from shared.validation import require_timezone_aware

logger = logging.getLogger(__name__)

TelemetryStatus = Literal[
    "available_fresh",
    "available_insufficient",
    "stale",
    "unavailable",
    "malformed",
]


class TelemetryResult(BaseModel):
    """Typed telemetry query result contract for observe-only controller."""

    query_started_at: datetime
    query_completed_at: datetime
    observation_start: datetime | None = None
    observation_end: datetime | None = None
    expected_interval_seconds: float = Field(gt=0.0)
    timestamps: list[datetime] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)
    observation_count: int = Field(ge=0)
    missing_interval_count: int = Field(default=0, ge=0)
    latest_age_seconds: float = Field(ge=0.0)
    is_fresh: bool = False
    is_sufficient: bool = False
    status: TelemetryStatus
    warnings: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_contract(self) -> "TelemetryResult":
        self.query_started_at = require_timezone_aware(self.query_started_at)
        self.query_completed_at = require_timezone_aware(self.query_completed_at)

        if self.observation_start is not None:
            self.observation_start = require_timezone_aware(self.observation_start)
        if self.observation_end is not None:
            self.observation_end = require_timezone_aware(self.observation_end)

        if len(self.timestamps) != len(self.values):
            raise ValueError(
                f"Length of timestamps ({len(self.timestamps)}) must equal "
                f"length of values ({len(self.values)})"
            )

        if self.observation_count != len(self.values):
            raise ValueError(
                f"observation_count ({self.observation_count}) must match "
                f"actual values count ({len(self.values)})"
            )

        for i, ts in enumerate(self.timestamps):
            require_timezone_aware(ts)
            if i > 0 and ts <= self.timestamps[i - 1]:
                raise ValueError(
                    f"timestamps must be strictly chronological: {ts} <= {self.timestamps[i - 1]}"
                )

        for val in self.values:
            if not np.isfinite(val):
                raise ValueError(f"Telemetry observation value must be finite; got {val}")
            if val < 0.0:
                raise ValueError(f"Telemetry observation value must be non-negative; got {val}")

        return self


class LiveTelemetryReader:
    """Retrieves and validates live request-rate history from Prometheus."""

    def __init__(
        self,
        config: ObserverConfig,
        client: PrometheusClient | None = None,
    ) -> None:
        self.config = config
        if client is not None:
            self.client = client
        else:
            self.client = PrometheusClient(
                base_url=self.config.prometheus_url,
                timeout_seconds=self.config.request_timeout_seconds,
                max_retries=self.config.maximum_retries,
                allow_non_local=False,
            )

    def read_history(
        self,
        current_time: datetime,
        minimum_history: int = 1,
    ) -> TelemetryResult:
        """Query Prometheus for recent request rate history and validate result."""
        curr_utc = require_timezone_aware(current_time)
        q_start = datetime.now(UTC)

        history_window = timedelta(seconds=self.config.history_window_seconds)
        range_start = curr_utc - history_window
        step_str = f"{int(self.config.sampling_interval_seconds)}s"

        warnings: list[str] = []
        try:
            query_res = self.client.query_range(
                field_name="request_rate_per_second",
                promql=self.config.query,
                start_time=range_start,
                end_time=curr_utc,
                step=step_str,
            )
        except Exception as exc:
            q_end = datetime.now(UTC)
            logger.warning("Prometheus telemetry query failed: %s", exc)
            return TelemetryResult(
                query_started_at=q_start,
                query_completed_at=q_end,
                expected_interval_seconds=self.config.sampling_interval_seconds,
                timestamps=[],
                values=[],
                observation_count=0,
                missing_interval_count=0,
                latest_age_seconds=float("inf"),
                is_fresh=False,
                is_sufficient=False,
                status="unavailable",
                warnings=[f"Prometheus query failed: {exc}"],
            )

        q_end = datetime.now(UTC)

        # Parse samples across series (expecting sum() so exactly 1 series when active)
        all_samples: list[tuple[datetime, float]] = []
        malformed = False

        for series in query_res.series:
            for s in series.samples:
                if s.value is None or not np.isfinite(s.value):
                    warnings.append(f"Non-finite sample encountered at {s.timestamp}")
                    malformed = True
                    continue
                if s.value < 0.0:
                    warnings.append(f"Negative sample value ({s.value}) at {s.timestamp}")
                    malformed = True
                    continue
                sample_ts = require_timezone_aware(s.timestamp)
                all_samples.append((sample_ts, float(s.value)))

        if malformed:
            return TelemetryResult(
                query_started_at=q_start,
                query_completed_at=q_end,
                expected_interval_seconds=self.config.sampling_interval_seconds,
                timestamps=[],
                values=[],
                observation_count=0,
                missing_interval_count=0,
                latest_age_seconds=float("inf"),
                is_fresh=False,
                is_sufficient=False,
                status="malformed",
                warnings=warnings,
            )

        if not all_samples:
            return TelemetryResult(
                query_started_at=q_start,
                query_completed_at=q_end,
                expected_interval_seconds=self.config.sampling_interval_seconds,
                timestamps=[],
                values=[],
                observation_count=0,
                missing_interval_count=0,
                latest_age_seconds=float("inf"),
                is_fresh=False,
                is_sufficient=False,
                status="unavailable",
                warnings=["No telemetry samples returned by Prometheus for query window"],
            )

        # Sort chronologically
        all_samples.sort(key=lambda item: item[0])

        # Deduplicate identical timestamps if present
        deduped: list[tuple[datetime, float]] = []
        for ts, val in all_samples:
            if deduped and deduped[-1][0] == ts:
                warnings.append(f"Duplicate timestamp detected and dropped at {ts}")
                continue
            deduped.append((ts, val))

        timestamps = [item[0] for item in deduped]
        values = [item[1] for item in deduped]
        obs_count = len(values)

        # Calculate missing intervals
        missing_intervals = 0
        interval = self.config.sampling_interval_seconds
        for i in range(1, len(timestamps)):
            delta_s = (timestamps[i] - timestamps[i - 1]).total_seconds()
            if delta_s > interval * 1.5:
                missed = max(0, int(round(delta_s / interval)) - 1)
                missing_intervals += missed

        latest_age_s = max(0.0, (curr_utc - timestamps[-1]).total_seconds())
        is_fresh = latest_age_s <= self.config.telemetry_stale_after_seconds
        is_sufficient = obs_count >= minimum_history

        if not is_fresh:
            status: TelemetryStatus = "stale"
            warnings.append(
                f"Latest observation age ({latest_age_s:.1f}s) exceeds stale threshold "
                f"({self.config.telemetry_stale_after_seconds:.1f}s)"
            )
        elif not is_sufficient:
            status = "available_insufficient"
            warnings.append(
                f"Observation count ({obs_count}) is below minimum history required ({minimum_history})"
            )
        else:
            status = "available_fresh"

        return TelemetryResult(
            query_started_at=q_start,
            query_completed_at=q_end,
            observation_start=timestamps[0],
            observation_end=timestamps[-1],
            expected_interval_seconds=interval,
            timestamps=timestamps,
            values=values,
            observation_count=obs_count,
            missing_interval_count=missing_intervals,
            latest_age_seconds=latest_age_s,
            is_fresh=is_fresh,
            is_sufficient=is_sufficient,
            status=status,
            warnings=warnings,
        )
