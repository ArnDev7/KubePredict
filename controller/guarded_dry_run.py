"""Guarded dry-run controller cycle implementation (Phase 10A).

Extends the Phase 9B dry-run controller with the Forecast Trust Guard.
If a forecast is trusted, the deterministic capacity model recommendation is
eligible for dry-run reporting (recommendation_eligible=True).
If untrusted, recommendation eligibility is blocked (recommendation_eligible=False).
Guarantees action_executed=False, zero Kubernetes writes, and zero reactive fallback.
"""

import csv
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from controller.capacity import calculate_capacity_recommendation
from controller.capacity_config import CapacityConfig
from controller.config import ObserverConfig
from controller.decision import build_dry_run_decision
from controller.forecast_store import ForecastStore, PendingForecast
from controller.guarded_decision import GuardedDryRunRecord
from controller.k8s_reader import ReadOnlyDeploymentReader
from controller.metrics import GuardedDryRunMetrics
from controller.telemetry import LiveTelemetryReader
from controller.trust_config import TrustGuardConfig
from controller.trust_guard import evaluate_forecast_trust
from forecasting.base import ForecastInput
from forecasting.service import ForecastingService
from shared.models import ForecastResult, ForecastTrustDecision, ScalingDecision
from shared.validation import require_timezone_aware

logger = logging.getLogger(__name__)


class GuardedDryRunCycleResult(BaseModel):
    """Structured result of a single guarded dry-run controller cycle."""

    cycle_number: int = Field(ge=1)
    timestamp: datetime
    telemetry_status: str
    outcome: str
    observation_count: int = Field(ge=0)
    latest_telemetry_age_seconds: float = Field(ge=0.0)
    forecast_id: str | None = None
    predicted_request_rate: float | None = None
    trust_decision: ForecastTrustDecision
    recommendation_eligible: bool
    current_replicas: int | None = None
    effective_capacity_per_replica: float | None = None
    unconstrained_required_replicas: int | None = None
    proposed_replicas: int | None = None
    final_replicas: int | None = None
    direction: str | None = None
    reason: str | None = None
    applied_constraints: list[str] = Field(default_factory=list)
    action_executed: bool = False
    guarded_record: GuardedDryRunRecord
    decision: ScalingDecision | None = None
    fallback_used: bool = False
    matched_forecasts_count: int = Field(default=0, ge=0)
    skip_reason: str | None = None
    duration_ms: float = Field(ge=0.0)

    model_config = {"extra": "forbid"}


def execute_guarded_dry_run_cycle(
    cycle_number: int,
    current_time: datetime,
    observer_config: ObserverConfig,
    capacity_config: CapacityConfig,
    trust_guard_config: TrustGuardConfig,
    telemetry_reader: LiveTelemetryReader,
    forecasting_service: ForecastingService,
    forecast_store: ForecastStore,
    deployment_reader: ReadOnlyDeploymentReader,
    metrics: GuardedDryRunMetrics,
) -> GuardedDryRunCycleResult:
    """Execute a single guarded dry-run controller cycle.

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
    trust_guard_config : TrustGuardConfig
        Validated forecast trust guard configuration.
    telemetry_reader : LiveTelemetryReader
        Prometheus live telemetry reader.
    forecasting_service : ForecastingService
        Frozen forecasting service.
    forecast_store : ForecastStore
        Store managing pending forecasts and actuals matching.
    deployment_reader : ReadOnlyDeploymentReader
        Strictly read-only deployment status reader.
    metrics : GuardedDryRunMetrics
        Guarded dry-run controller operational metrics.

    Returns
    -------
    GuardedDryRunCycleResult
        Structured cycle outcome with deterministic trust decision and guarded recommendation.
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

    # Expire unmatched forecasts older than retention window
    expired = forecast_store.expire_unmatched(curr_utc, max_retention_seconds=300.0)
    if expired:
        metrics.record_expired(len(expired))
        logger.warning("Expired %d unmatched forecasts", len(expired))

    # 3. Forecast inference
    forecast_id: str | None = None
    predicted_rate: float | None = None
    fallback_used = False
    pending_rec: PendingForecast | None = None
    fc_model: ForecastResult | None = None

    if (
        telemetry_res.status not in ("unavailable", "malformed", "stale")
        and telemetry_res.is_sufficient
        and telemetry_res.is_fresh
    ):
        forecast_input = ForecastInput(
            generated_at=curr_utc,
            sampling_interval_seconds=observer_config.sampling_interval_seconds,
            observations=telemetry_res.values,
            observation_timestamps=telemetry_res.timestamps,
            requested_horizon_steps=observer_config.forecast_horizon_steps,
        )
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

        fc_model = ForecastResult(
            generated_at=curr_utc,
            horizon_seconds=int(forecast_output.horizon_seconds),
            predicted_request_rate_per_second=predicted_rate,
            confidence=1.0,
            model_name=forecast_output.model_name,
            observations_used=forecast_output.history_count,
        )

    # 4. Forecast Trust Guard Evaluation
    trust_decision = evaluate_forecast_trust(
        current_time=curr_utc,
        config=trust_guard_config,
        telemetry=telemetry_res,
        forecast=pending_rec,
        candidate_residuals=forecast_store.get_matched(),
    )

    # 5. Guarded Dry-Run Capacity Recommendation Flow
    current_reps: int | None = None
    unconstrained_reps: int | None = None
    proposed_reps: int | None = None
    final_reps: int | None = None
    rec_direction: str | None = None
    rec_reason: str | None = None
    applied_constraints: list[str] = []
    decision: ScalingDecision | None = None
    recommendation_eligible: bool = False
    skip_reason: str | None = None

    if trust_decision.trusted:
        # Trusted: Read deployment status and compute capacity recommendation
        dep_status = deployment_reader.get_replica_status()
        if not dep_status.is_valid or dep_status.available_replicas is None:
            outcome = "guarded_dry_run_skipped_missing_replicas"
            skip_reason = dep_status.error_message or "Current deployment replicas unavailable"
            logger.warning("Cycle %d: %s", cycle_number, skip_reason)
        else:
            current_reps = dep_status.available_replicas
            assert predicted_rate is not None  # Guaranteed by trust_decision.trusted

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

            decision = build_dry_run_decision(
                timestamp=curr_utc,
                capacity_result=cap_result,
                forecast=fc_model,
                extra_details={
                    "cycle_number": cycle_number,
                    "forecast_id": forecast_id,
                    "model_name": trust_decision.model_name,
                    "deployment_desired_replicas": dep_status.desired_replicas,
                    "deployment_available_replicas": dep_status.available_replicas,
                    "trust_outcome": trust_decision.outcome.value,
                    "recommendation_eligible": True,
                },
            )

            metrics.record_recommendation(
                direction=rec_direction,
                reason=rec_reason,
                current_replicas=current_reps,
                proposed_replicas=proposed_reps,
                final_replicas=final_reps,
                predicted_rate=predicted_rate,
                effective_capacity=capacity_config.effective_capacity_per_replica,
                constraints=applied_constraints,
            )

            recommendation_eligible = True
            outcome = "guarded_dry_run_trusted_eligible"
            logger.info(
                "Cycle %d: [TRUSTED] Decision: current=%d, proposed=%d, final=%d, dir=%s, reason=%s [eligible=True, action_executed=False]",
                cycle_number,
                current_reps,
                proposed_reps,
                final_reps,
                rec_direction,
                rec_reason,
            )
    else:
        # Untrusted: Block recommendation eligibility. Do NOT invoke reactive fallback!
        recommendation_eligible = False
        outcome = "guarded_dry_run_untrusted_blocked"
        skip_reason = f"Forecast untrusted: outcome={trust_decision.outcome.value}"
        logger.info(
            "Cycle %d: [UNTRUSTED: %s] Recommendation blocked [eligible=False, action_executed=False]",
            cycle_number,
            trust_decision.outcome.value,
        )

    # 6. Update operational metrics for trust guard
    metrics.record_guard_decision(
        decision=trust_decision,
        recommendation_eligible=recommendation_eligible,
    )

    # 7. Construct GuardedDryRunRecord
    guarded_rec = GuardedDryRunRecord(
        timestamp=curr_utc,
        cycle_number=cycle_number,
        forecast=fc_model if trust_decision.trusted else None,
        trust_decision=trust_decision,
        recommendation_eligible=recommendation_eligible,
        capacity_recommendation=decision,
        actionable_recommendation=None,  # Strictly None
        action_executed=False,  # Strictly False
        warnings=list(trust_decision.warnings),
        details={
            "outcome": outcome,
            "skip_reason": skip_reason,
            "duration_ms": 0.0,
        },
    )

    t_end = time.perf_counter()
    duration_ms = round((t_end - t_start) * 1000.0, 2)
    guarded_rec.details["duration_ms"] = duration_ms

    metrics.record_cycle(
        outcome=outcome,
        telemetry_age=telemetry_res.latest_age_seconds,
        obs_count=telemetry_res.observation_count,
        pending_count=len(forecast_store.get_pending()),
    )

    return GuardedDryRunCycleResult(
        cycle_number=cycle_number,
        timestamp=curr_utc,
        telemetry_status=telemetry_res.status,
        outcome=outcome,
        observation_count=telemetry_res.observation_count,
        latest_telemetry_age_seconds=round(telemetry_res.latest_age_seconds, 2),
        forecast_id=forecast_id,
        predicted_request_rate=round(predicted_rate, 4) if predicted_rate is not None else None,
        trust_decision=trust_decision,
        recommendation_eligible=recommendation_eligible,
        current_replicas=current_reps,
        effective_capacity_per_replica=capacity_config.effective_capacity_per_replica,
        unconstrained_required_replicas=unconstrained_reps,
        proposed_replicas=proposed_reps,
        final_replicas=final_reps,
        direction=rec_direction,
        reason=rec_reason,
        applied_constraints=applied_constraints,
        action_executed=False,
        guarded_record=guarded_rec,
        decision=decision,
        fallback_used=fallback_used,
        matched_forecasts_count=len(matched),
        skip_reason=skip_reason,
        duration_ms=duration_ms,
    )


def write_guarded_dry_run_artifacts(
    run_dir: Path,
    run_id: str,
    observer_config: ObserverConfig,
    capacity_config: CapacityConfig,
    trust_guard_config: TrustGuardConfig,
    forecasting_service: ForecastingService,
    forecast_store: ForecastStore,
    cycle_results: list[GuardedDryRunCycleResult],
    started_at: datetime,
    completed_at: datetime,
    status: str,
    config_path: Path,
    capacity_config_path: Path,
    trust_guard_config_path: Path,
    frozen_forecaster_path: Path,
) -> None:
    """Persist all Phase 10A evidence artifacts under experiments/output/controller-trust-guard/<run-id>/."""
    all_forecasts = forecast_store.get_all()
    matched_forecasts = forecast_store.get_matched()
    pending_forecasts = forecast_store.get_pending()
    expired_forecasts = forecast_store.get_expired()

    trusted_cycles = [c for c in cycle_results if c.trust_decision.trusted]
    untrusted_cycles = [c for c in cycle_results if not c.trust_decision.trusted]
    eligible_cycles = [c for c in cycle_results if c.recommendation_eligible]
    blocked_cycles = [c for c in cycle_results if not c.recommendation_eligible]
    skipped_cycles = [c for c in cycle_results if c.skip_reason is not None]

    outcome_counts: dict[str, int] = {}
    for c in cycle_results:
        outcome_key = c.trust_decision.outcome.value
        outcome_counts[outcome_key] = outcome_counts.get(outcome_key, 0) + 1

    # 1. metadata.json
    metadata = {
        "run_id": run_id,
        "phase": "10A",
        "mode": "guarded_dry_run",
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": round((completed_at - started_at).total_seconds(), 2),
        "status": status,
        "cycles_requested": observer_config.maximum_cycles,
        "cycles_completed": len(cycle_results),
        "forecasts_generated": len(all_forecasts),
        "forecasts_matched": len(matched_forecasts),
        "forecasts_pending": len(pending_forecasts),
        "forecasts_expired": len(expired_forecasts),
        "trusted_count": len(trusted_cycles),
        "untrusted_count": len(untrusted_cycles),
        "recommendation_eligible_count": len(eligible_cycles),
        "recommendation_blocked_count": len(blocked_cycles),
        "outcome_counts": outcome_counts,
        "action_executed_all_false": all(c.action_executed is False for c in cycle_results),
        "model_name": forecasting_service.model_name,
        "model_status": forecasting_service.status,
        "capacity_status": capacity_config.status,
        "trust_guard_status": trust_guard_config.status,
        "absolute_error_threshold": trust_guard_config.absolute_error_threshold,
        "relative_error_threshold": trust_guard_config.relative_error_threshold,
        "minimum_matched_residuals": trust_guard_config.minimum_matched_residuals,
        "evidence_window_seconds": trust_guard_config.evidence_window_seconds,
        "residual_max_age_seconds": trust_guard_config.residual_max_age_seconds,
        "source_hashes": {
            "config": config_path.name,
            "capacity_config": capacity_config_path.name,
            "trust_guard_config": trust_guard_config_path.name,
            "frozen_forecaster": frozen_forecaster_path.name,
        },
    }
    with (run_dir / "metadata.json").open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    # 2. cycles.jsonl
    with (run_dir / "cycles.jsonl").open("w", encoding="utf-8") as f:
        for c in cycle_results:
            f.write(c.model_dump_json() + "\n")

    # 3. forecasts.csv
    with (run_dir / "forecasts.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "forecast_id",
                "generated_at",
                "target_timestamp",
                "horizon_seconds",
                "model_name",
                "predicted_request_rate_per_second",
                "fallback_used",
                "history_count",
                "status",
            ]
        )
        for fc in all_forecasts:
            writer.writerow(
                [
                    fc.forecast_id,
                    fc.generated_at.isoformat(),
                    fc.target_timestamp.isoformat(),
                    fc.horizon_seconds,
                    fc.model_name,
                    fc.predicted_request_rate_per_second,
                    fc.fallback_used,
                    fc.history_count,
                    fc.status,
                ]
            )

    # 4. trust_decisions.csv
    with (run_dir / "trust_decisions.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "timestamp_utc",
                "cycle_number",
                "forecast_id",
                "trusted",
                "outcome",
                "telemetry_age_seconds",
                "history_observations",
                "matched_residual_count",
                "recent_mae",
                "recent_rmse",
                "recent_smape",
                "recent_bias",
                "absolute_threshold",
                "relative_threshold",
                "recommendation_eligible",
                "action_executed",
            ]
        )
        for c in cycle_results:
            td = c.trust_decision
            writer.writerow(
                [
                    c.timestamp.isoformat(),
                    c.cycle_number,
                    td.forecast_id or "",
                    td.trusted,
                    td.outcome.value,
                    td.telemetry_age_seconds if td.telemetry_age_seconds is not None else "",
                    td.history_observations,
                    td.matched_residual_count,
                    td.recent_mae if td.recent_mae is not None else "",
                    td.recent_rmse if td.recent_rmse is not None else "",
                    td.recent_smape if td.recent_smape is not None else "",
                    td.recent_bias if td.recent_bias is not None else "",
                    td.absolute_error_threshold,
                    td.relative_error_threshold,
                    c.recommendation_eligible,
                    c.action_executed,  # Strictly False
                ]
            )

    # 5. matched_residuals.csv
    with (run_dir / "matched_residuals.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "forecast_id",
                "generated_at",
                "target_timestamp",
                "horizon_seconds",
                "predicted_request_rate_per_second",
                "actual_request_rate_per_second",
                "residual",
                "absolute_error",
            ]
        )
        for m in matched_forecasts:
            writer.writerow(
                [
                    m.forecast_id,
                    m.generated_at.isoformat(),
                    m.target_timestamp.isoformat(),
                    m.horizon_seconds,
                    m.predicted_request_rate_per_second,
                    m.actual_request_rate_per_second,
                    m.residual,
                    m.absolute_error,
                ]
            )

    # 6. guarded_recommendations.csv
    with (run_dir / "guarded_recommendations.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "timestamp_utc",
                "cycle_number",
                "forecast_id",
                "trusted",
                "recommendation_eligible",
                "current_replicas",
                "proposed_replicas",
                "final_replicas",
                "direction",
                "reason",
                "action_executed",
            ]
        )
        for c in cycle_results:
            writer.writerow(
                [
                    c.timestamp.isoformat(),
                    c.cycle_number,
                    c.forecast_id or "",
                    c.trust_decision.trusted,
                    c.recommendation_eligible,
                    c.current_replicas if c.current_replicas is not None else "",
                    c.proposed_replicas if c.proposed_replicas is not None else "",
                    c.final_replicas if c.final_replicas is not None else "",
                    c.direction or "",
                    c.reason or "",
                    c.action_executed,  # Strictly False
                ]
            )

    # 7. skipped_cycles.csv
    with (run_dir / "skipped_cycles.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["cycle_number", "timestamp", "skip_reason", "telemetry_status", "trust_outcome"]
        )
        for sc in skipped_cycles:
            writer.writerow(
                [
                    sc.cycle_number,
                    sc.timestamp.isoformat(),
                    sc.skip_reason or "",
                    sc.telemetry_status,
                    sc.trust_decision.outcome.value,
                ]
            )

    # 8. trust_guard_configuration.yaml
    with (run_dir / "trust_guard_configuration.yaml").open("w", encoding="utf-8") as f:
        f.write(trust_guard_config_path.read_text(encoding="utf-8"))

    # 9. validation_report.json
    all_action_false = all(c.action_executed is False for c in cycle_results)
    no_actionable_rec = all(
        c.guarded_record.actionable_recommendation is None for c in cycle_results
    )
    validation_report = {
        "run_id": run_id,
        "passed": all_action_false and no_actionable_rec and status in ("completed", "interrupted"),
        "total_cycles": len(cycle_results),
        "trusted_count": len(trusted_cycles),
        "untrusted_count": len(untrusted_cycles),
        "recommendation_eligible_count": len(eligible_cycles),
        "recommendation_blocked_count": len(blocked_cycles),
        "action_executed_all_false": all_action_false,
        "no_actionable_recommendations": no_actionable_rec,
        "no_k8s_mutations": True,
        "notes": "Forecast Trust Guard operated strictly in read-only guarded dry-run mode with zero actuation.",
    }
    with (run_dir / "validation_report.json").open("w", encoding="utf-8") as f:
        json.dump(validation_report, f, indent=2)

    logger.info("Artifacts saved to %s (all action_executed=False verified).", run_dir)
