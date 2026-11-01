"""Observe-only controller cycle implementation (Phase 9A).

Strictly observe-only: reads telemetry, generates forecasts via frozen service,
registers pending forecasts, and matches later actuals for residual evidence.
Contains zero Kubernetes API writes, zero replica calculations, and zero scaling logic.
"""

import logging
import time
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from controller.config import ObserverConfig
from controller.forecast_store import ForecastStore, PendingForecast
from controller.metrics import ObserverMetrics
from controller.telemetry import LiveTelemetryReader
from forecasting.base import ForecastInput
from forecasting.service import ForecastingService
from shared.validation import require_timezone_aware

logger = logging.getLogger(__name__)


class ObserverCycleResult(BaseModel):
    """Structured result of a single observe-only controller cycle."""

    cycle_number: int = Field(ge=1)
    timestamp: datetime
    telemetry_status: str
    outcome: str
    observation_count: int = Field(ge=0)
    latest_telemetry_age_seconds: float = Field(ge=0.0)
    forecast_id: str | None = None
    predicted_request_rate: float | None = None
    fallback_used: bool = False
    matched_forecasts_count: int = Field(default=0, ge=0)
    skip_reason: str | None = None
    duration_ms: float = Field(ge=0.0)

    model_config = {"extra": "forbid"}


def execute_observer_cycle(
    cycle_number: int,
    current_time: datetime,
    config: ObserverConfig,
    telemetry_reader: LiveTelemetryReader,
    forecasting_service: ForecastingService,
    forecast_store: ForecastStore,
    metrics: ObserverMetrics,
) -> ObserverCycleResult:
    """Execute a single observe-only controller cycle.

    Parameters
    ----------
    cycle_number : int
        Sequential 1-based cycle index.
    current_time : datetime
        Current UTC timestamp for this cycle.
    config : ObserverConfig
        Validated observer configuration.
    telemetry_reader : LiveTelemetryReader
        Prometheus live telemetry reader.
    forecasting_service : ForecastingService
        Frozen forecasting service.
    forecast_store : ForecastStore
        Store managing pending forecasts and matching.
    metrics : ObserverMetrics
        Controller operational metrics.

    Returns
    -------
    ObserverCycleResult
        Structured cycle execution outcome.
    """
    curr_utc = require_timezone_aware(current_time)
    t_start = time.perf_counter()

    # 1. Retrieve recent history from Prometheus
    # If persistence fallback is enabled, minimum required history is 1 sample
    min_history = (
        1
        if forecasting_service.persistence_fallback_enabled
        else forecasting_service.model.minimum_history_required
    )
    telemetry_res = telemetry_reader.read_history(curr_utc, minimum_history=min_history)

    # 2. Match pending forecasts whose target time has arrived with available telemetry
    matched = forecast_store.match_actuals(
        current_time=curr_utc,
        observation_timestamps=telemetry_res.timestamps,
        observation_values=telemetry_res.values,
        tolerance_seconds=config.sampling_interval_seconds,
    )
    for m in matched:
        if m.actual_request_rate_per_second is not None and m.absolute_error is not None:
            metrics.record_matched(
                actual_val=m.actual_request_rate_per_second,
                abs_err=m.absolute_error,
            )
            logger.info(
                "Matched forecast %s: predicted=%.4f, actual=%.4f, residual=%.4f, abs_err=%.4f",
                m.forecast_id,
                m.predicted_request_rate_per_second,
                m.actual_request_rate_per_second,
                m.residual if m.residual is not None else 0.0,
                m.absolute_error,
            )

    # Expire any orphaned forecasts older than 5 minutes
    expired = forecast_store.expire_unmatched(curr_utc, max_retention_seconds=300.0)
    if expired:
        metrics.record_expired(len(expired))
        logger.warning("Expired %d unmatched forecasts", len(expired))

    # 3. Evaluate telemetry availability and generate forecast if eligible
    forecast_id: str | None = None
    predicted_rate: float | None = None
    fallback_used = False
    skip_reason: str | None = None
    outcome: str

    if telemetry_res.status == "unavailable":
        outcome = "forecast_skipped_unavailable"
        skip_reason = "; ".join(telemetry_res.warnings) or "Prometheus telemetry unavailable"
    elif telemetry_res.status == "stale":
        outcome = "forecast_skipped_stale_telemetry"
        skip_reason = "; ".join(telemetry_res.warnings) or "Latest telemetry observation is stale"
    elif telemetry_res.status == "available_insufficient" or not telemetry_res.is_sufficient:
        outcome = "forecast_skipped_insufficient_history"
        skip_reason = (
            "; ".join(telemetry_res.warnings)
            or f"Observations count ({telemetry_res.observation_count}) below minimum required ({min_history})"
        )
    elif not telemetry_res.is_fresh:
        outcome = "forecast_skipped_stale_telemetry"
        skip_reason = "Telemetry freshness verification failed"
    else:
        # Construct ForecastInput adhering to runtime contract
        forecast_input = ForecastInput(
            generated_at=curr_utc,
            sampling_interval_seconds=config.sampling_interval_seconds,
            observations=telemetry_res.values,
            observation_timestamps=telemetry_res.timestamps,
            requested_horizon_steps=config.forecast_horizon_steps,
        )

        # Pure in-process forecast inference
        forecast_output = forecasting_service.predict(forecast_input)

        target_ts = curr_utc + timedelta(seconds=forecast_output.horizon_seconds)
        pending_rec = PendingForecast(
            generated_at=curr_utc,
            target_timestamp=target_ts,
            horizon_seconds=forecast_output.horizon_seconds,
            model_name=forecast_output.model_name,
            predicted_request_rate_per_second=forecast_output.predicted_request_rate_per_second,
            fallback_used=forecast_output.fallback_used,
            history_count=forecast_output.history_count,
            warnings=forecast_output.warnings,
        )
        forecast_store.add(pending_rec)

        forecast_id = pending_rec.forecast_id
        predicted_rate = pending_rec.predicted_request_rate_per_second
        fallback_used = pending_rec.fallback_used
        outcome = "forecast_created"

        metrics.record_forecast(
            model_name=forecast_output.model_name,
            fallback_used=forecast_output.fallback_used,
            predicted_val=forecast_output.predicted_request_rate_per_second,
        )

        logger.info(
            "Cycle %d: Generated forecast %s (%s, fallback=%s): %.4f req/s (horizon %ds)",
            cycle_number,
            forecast_id,
            forecast_output.model_name,
            fallback_used,
            predicted_rate,
            int(forecast_output.horizon_seconds),
        )

    t_end = time.perf_counter()
    duration_ms = round((t_end - t_start) * 1000.0, 2)

    # Record operational metrics
    metrics.record_cycle(
        outcome=outcome,
        telemetry_age=telemetry_res.latest_age_seconds,
        obs_count=telemetry_res.observation_count,
        pending_count=len(forecast_store.get_pending()),
    )

    return ObserverCycleResult(
        cycle_number=cycle_number,
        timestamp=curr_utc,
        telemetry_status=telemetry_res.status,
        outcome=outcome,
        observation_count=telemetry_res.observation_count,
        latest_telemetry_age_seconds=round(telemetry_res.latest_age_seconds, 2),
        forecast_id=forecast_id,
        predicted_request_rate=round(predicted_rate, 4) if predicted_rate is not None else None,
        fallback_used=fallback_used,
        matched_forecasts_count=len(matched),
        skip_reason=skip_reason,
        duration_ms=duration_ms,
    )
