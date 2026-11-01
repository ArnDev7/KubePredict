"""Dry-run controller cycle implementation (Phase 9B).

Extends the Phase 9A observer with a transparent capacity model and
read-only deployment status reader to generate dry-run replica recommendations.
Contains zero Kubernetes write calls, zero scaling actuation, and guarantees action_executed=False.
"""

import logging
import time
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from controller.capacity import calculate_capacity_recommendation
from controller.capacity_config import CapacityConfig
from controller.config import ObserverConfig
from controller.decision import build_dry_run_decision
from controller.forecast_store import ForecastStore, PendingForecast
from controller.k8s_reader import ReadOnlyDeploymentReader
from controller.metrics import DryRunMetrics
from controller.telemetry import LiveTelemetryReader
from forecasting.base import ForecastInput
from forecasting.service import ForecastingService
from shared.models import ForecastResult, ScalingDecision
from shared.validation import require_timezone_aware

logger = logging.getLogger(__name__)


class DryRunCycleResult(BaseModel):
    """Structured result of a single dry-run controller cycle."""

    cycle_number: int = Field(ge=1)
    timestamp: datetime
    telemetry_status: str
    outcome: str
    observation_count: int = Field(ge=0)
    latest_telemetry_age_seconds: float = Field(ge=0.0)
    forecast_id: str | None = None
    predicted_request_rate: float | None = None
    current_replicas: int | None = None
    effective_capacity_per_replica: float | None = None
    unconstrained_required_replicas: int | None = None
    proposed_replicas: int | None = None
    final_replicas: int | None = None
    direction: str | None = None
    reason: str | None = None
    applied_constraints: list[str] = Field(default_factory=list)
    action_executed: bool = False
    decision: ScalingDecision | None = None
    fallback_used: bool = False
    matched_forecasts_count: int = Field(default=0, ge=0)
    skip_reason: str | None = None
    duration_ms: float = Field(ge=0.0)

    model_config = {"extra": "forbid"}


def execute_dry_run_cycle(
    cycle_number: int,
    current_time: datetime,
    observer_config: ObserverConfig,
    capacity_config: CapacityConfig,
    telemetry_reader: LiveTelemetryReader,
    forecasting_service: ForecastingService,
    forecast_store: ForecastStore,
    deployment_reader: ReadOnlyDeploymentReader,
    metrics: DryRunMetrics,
) -> DryRunCycleResult:
    """Execute a single dry-run controller cycle with capacity recommendation.

    Parameters
    ----------
    cycle_number : int
        Sequential 1-based cycle index.
    current_time : datetime
        Current UTC timestamp for this cycle.
    observer_config : ObserverConfig
        Validated observer configuration.
    capacity_config : CapacityConfig
        Validated capacity model configuration.
    telemetry_reader : LiveTelemetryReader
        Prometheus live telemetry reader.
    forecasting_service : ForecastingService
        Frozen forecasting service.
    forecast_store : ForecastStore
        Store managing pending forecasts and actuals matching.
    deployment_reader : ReadOnlyDeploymentReader
        Strictly read-only deployment status reader.
    metrics : DryRunMetrics
        Dry-run controller operational metrics.

    Returns
    -------
    DryRunCycleResult
        Structured cycle outcome with dry-run scaling recommendation (action_executed=False).
    """
    curr_utc = require_timezone_aware(current_time)
    t_start = time.perf_counter()

    # 1. Retrieve recent history from Prometheus
    min_history = (
        1
        if forecasting_service.persistence_fallback_enabled
        else forecasting_service.model.minimum_history_required
    )
    telemetry_res = telemetry_reader.read_history(curr_utc, minimum_history=min_history)

    # 2. Match pending forecasts whose target time has arrived
    matched = forecast_store.match_actuals(
        current_time=curr_utc,
        observation_timestamps=telemetry_res.timestamps,
        observation_values=telemetry_res.values,
        tolerance_seconds=observer_config.sampling_interval_seconds,
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

    # Expire orphaned forecasts older than 5 minutes
    expired = forecast_store.expire_unmatched(curr_utc, max_retention_seconds=300.0)
    if expired:
        metrics.record_expired(len(expired))
        logger.warning("Expired %d unmatched forecasts", len(expired))

    # 3. Forecast generation and capacity recommendation
    forecast_id: str | None = None
    predicted_rate: float | None = None
    fallback_used = False
    skip_reason: str | None = None
    outcome: str
    decision: ScalingDecision | None = None
    current_reps: int | None = None
    unconstrained_reps: int | None = None
    proposed_reps: int | None = None
    final_reps: int | None = None
    rec_direction: str | None = None
    rec_reason: str | None = None
    applied_constraints: list[str] = []

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
        # Construct ForecastInput
        forecast_input = ForecastInput(
            generated_at=curr_utc,
            sampling_interval_seconds=observer_config.sampling_interval_seconds,
            observations=telemetry_res.values,
            observation_timestamps=telemetry_res.timestamps,
            requested_horizon_steps=observer_config.forecast_horizon_steps,
        )

        # In-process forecast inference
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

        metrics.record_forecast(
            model_name=forecast_output.model_name,
            fallback_used=forecast_output.fallback_used,
            predicted_val=forecast_output.predicted_request_rate_per_second,
        )

        # 4. Read current deployment replicas through read-only orchestration
        dep_status = deployment_reader.get_replica_status()
        if not dep_status.is_valid or dep_status.available_replicas is None:
            outcome = "dry_run_skipped_missing_replicas"
            skip_reason = dep_status.error_message or "Current deployment replicas unavailable"
            logger.warning("Cycle %d: %s", cycle_number, skip_reason)
        else:
            current_reps = dep_status.available_replicas

            # 5. Evaluate pure deterministic capacity model
            cap_result = calculate_capacity_recommendation(
                predicted_request_rate_per_second=predicted_rate,
                current_replicas=current_reps,
                config=capacity_config,
            )

            unconstrained_reps = cap_result.unconstrained_required_replicas
            proposed_reps = cap_result.proposed_replicas
            final_reps = cap_result.final_replicas
            rec_direction = cap_result.direction.value
            rec_reason = cap_result.reason.value
            applied_constraints = list(cap_result.applied_constraints)

            # Build ForecastResult for decision embedding
            fc_model = ForecastResult(
                generated_at=curr_utc,
                horizon_seconds=int(forecast_output.horizon_seconds),
                predicted_request_rate_per_second=predicted_rate,
                confidence=1.0,  # Forecast model confidence placeholder
                model_name=forecast_output.model_name,
                observations_used=forecast_output.history_count,
            )

            # Build dry-run decision
            decision = build_dry_run_decision(
                timestamp=curr_utc,
                capacity_result=cap_result,
                forecast=fc_model,
                extra_details={
                    "cycle_number": cycle_number,
                    "forecast_id": forecast_id,
                    "model_name": forecast_output.model_name,
                    "deployment_desired_replicas": dep_status.desired_replicas,
                    "deployment_available_replicas": dep_status.available_replicas,
                },
            )

            # 6. Update dry-run operational metrics
            metrics.record_recommendation(
                direction=rec_direction,
                reason=rec_reason,
                current_replicas=current_reps,
                proposed_replicas=proposed_reps,
                final_replicas=final_reps,
                predicted_rate=predicted_rate,
                effective_capacity=cap_result.effective_capacity_per_replica,
                constraints=applied_constraints,
            )

            outcome = "dry_run_recommendation_created"
            logger.info(
                "Cycle %d: Dry-Run Decision: current=%d, unconstrained=%d, proposed=%d, final=%d, "
                "dir=%s, reason=%s, constraints=%s [action_executed=False]",
                cycle_number,
                current_reps,
                unconstrained_reps,
                proposed_reps,
                final_reps,
                rec_direction,
                rec_reason,
                applied_constraints,
            )

    t_end = time.perf_counter()
    duration_ms = round((t_end - t_start) * 1000.0, 2)

    # Record operational cycle metric
    metrics.record_cycle(
        outcome=outcome,
        telemetry_age=telemetry_res.latest_age_seconds,
        obs_count=telemetry_res.observation_count,
        pending_count=len(forecast_store.get_pending()),
    )

    return DryRunCycleResult(
        cycle_number=cycle_number,
        timestamp=curr_utc,
        telemetry_status=telemetry_res.status,
        outcome=outcome,
        observation_count=telemetry_res.observation_count,
        latest_telemetry_age_seconds=round(telemetry_res.latest_age_seconds, 2),
        forecast_id=forecast_id,
        predicted_request_rate=round(predicted_rate, 4) if predicted_rate is not None else None,
        current_replicas=current_reps,
        effective_capacity_per_replica=capacity_config.effective_capacity_per_replica,
        unconstrained_required_replicas=unconstrained_reps,
        proposed_replicas=proposed_reps,
        final_replicas=final_reps,
        direction=rec_direction,
        reason=rec_reason,
        applied_constraints=applied_constraints,
        action_executed=False,  # Strictly guaranteed False
        decision=decision,
        fallback_used=fallback_used,
        matched_forecasts_count=len(matched),
        skip_reason=skip_reason,
        duration_ms=duration_ms,
    )
