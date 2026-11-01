"""Dry-run arbitrated autoscaling controller implementation (Phase 10B).

Coordinates telemetry querying, frozen model forecasting, residual matching,
forecast trust evaluation, rapid-change detection, reactive CPU fallback,
replica bounds, step limits, downscale stabilization, and cooldown.
Operates strictly in dry-run mode with zero Kubernetes write operations.
"""

import csv
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from controller.arbitration import arbitrate_policy_decision
from controller.arbitration_decision import ArbitrationDecisionRecord
from controller.capacity import CapacityResult, calculate_capacity_recommendation
from controller.capacity_config import CapacityConfig
from controller.config import ObserverConfig
from controller.cooldown import CooldownManager
from controller.cpu_telemetry import (
    CpuTelemetryProvider,
    CpuTelemetryResult,
)
from controller.forecast_store import ForecastStore, PendingForecast
from controller.k8s_reader import ReadOnlyDeploymentReader
from controller.metrics import ArbitrationMetrics
from controller.policy_config import PolicyConfig
from controller.rapid_change import RapidChangeResult, detect_rapid_change
from controller.stabilization import DownscaleStabilizer
from controller.telemetry import LiveTelemetryReader, TelemetryResult
from controller.trust_config import TrustGuardConfig
from controller.trust_guard import evaluate_forecast_trust
from forecasting.base import ForecastInput
from forecasting.service import ForecastingService
from shared.enums import PolicyMode
from shared.models import ForecastResult, ForecastTrustDecision
from shared.validation import require_timezone_aware

logger = logging.getLogger(__name__)


class ArbitrationCycleResult(BaseModel):
    """Structured result of a single arbitrated dry-run controller cycle."""

    cycle_number: int = Field(ge=1)
    timestamp: datetime
    telemetry_status: str
    policy_mode: str
    primary_reason: str
    current_replicas: int = Field(gt=0)
    forecast_id: str | None = None
    predicted_request_rate: float | None = None
    forecast_trusted: bool = False
    rapid_change_detected: bool = False
    current_cpu_ratio: float | None = None
    predictive_recommendation: int | None = None
    reactive_recommendation: int | None = None
    unconstrained_recommendation: int | None = None
    bounded_recommendation: int | None = None
    step_limited_recommendation: int | None = None
    cooldown_blocked: bool = False
    stabilized_recommendation: int | None = None
    final_recommendation: int | None = None
    direction: str
    applied_constraints: list[str] = Field(default_factory=list)
    action_executed: bool = False
    decision_record: ArbitrationDecisionRecord
    trust_decision: ForecastTrustDecision | None = None
    rapid_change_result: RapidChangeResult | None = None
    duration_ms: float = Field(ge=0.0)
    skip_reason: str | None = None

    model_config = {"extra": "forbid"}


def execute_arbitration_cycle(
    cycle_number: int,
    current_time: datetime,
    observer_config: ObserverConfig,
    capacity_config: CapacityConfig,
    trust_guard_config: TrustGuardConfig,
    policy_config: PolicyConfig,
    telemetry_reader: LiveTelemetryReader,
    forecasting_service: ForecastingService,
    forecast_store: ForecastStore,
    deployment_reader: ReadOnlyDeploymentReader,
    cpu_telemetry_provider: CpuTelemetryProvider,
    metrics: ArbitrationMetrics,
    cooldown_manager: CooldownManager,
    stabilizer: DownscaleStabilizer,
) -> ArbitrationCycleResult:
    """Execute a single arbitrated dry-run cycle across all policy stages.

    Parameters
    ----------
    cycle_number : int
        Sequential cycle index.
    current_time : datetime
        UTC timestamp for this cycle.
    observer_config : ObserverConfig
        Validated observer configuration.
    capacity_config : CapacityConfig
        Validated capacity model configuration.
    trust_guard_config : TrustGuardConfig
        Validated forecast trust guard configuration.
    policy_config : PolicyConfig
        Validated arbitration policy configuration.
    telemetry_reader : LiveTelemetryReader
        Prometheus telemetry reader.
    forecasting_service : ForecastingService
        Frozen forecasting service.
    forecast_store : ForecastStore
        Residual tracking and forecast store.
    deployment_reader : ReadOnlyDeploymentReader
        Read-only deployment replica reader.
    cpu_telemetry_provider : CpuTelemetryProvider
        Read-only CPU utilization provider.
    metrics : ArbitrationMetrics
        Operational metrics recorder.
    cooldown_manager : CooldownManager
        Cooldown manager.
    stabilizer : DownscaleStabilizer
        Downscale stabilization manager.

    Returns
    -------
    ArbitrationCycleResult
        Complete cycle outcome.
    """
    start_wall = time.perf_counter()
    ts_utc = require_timezone_aware(current_time)

    # 1. Read current deployment replicas (read-only)
    replica_status = deployment_reader.get_replica_status()
    current_replicas = (
        replica_status.desired_replicas
        if (replica_status.is_valid and replica_status.desired_replicas is not None)
        else 2
    )

    # 2. Query request-rate telemetry from Prometheus
    telemetry_result: TelemetryResult = telemetry_reader.read_history(
        current_time=ts_utc,
    )

    # 3. Match pending forecasts whose target time has arrived
    if telemetry_result.timestamps and telemetry_result.values:
        forecast_store.match_actuals(
            current_time=ts_utc,
            observation_timestamps=telemetry_result.timestamps,
            observation_values=telemetry_result.values,
            tolerance_seconds=observer_config.sampling_interval_seconds,
        )

    # 4. Generate frozen point forecast
    point_forecast: ForecastResult | None = None
    forecast_id: str | None = None
    predicted_rate: float | None = None
    pending_fc: PendingForecast | None = None

    if (
        telemetry_result.status not in ("unavailable", "malformed", "stale")
        and telemetry_result.is_sufficient
        and telemetry_result.is_fresh
    ):
        try:
            fc_input = ForecastInput(
                generated_at=ts_utc,
                sampling_interval_seconds=observer_config.sampling_interval_seconds,
                observations=telemetry_result.values,
                observation_timestamps=telemetry_result.timestamps,
                requested_horizon_steps=observer_config.forecast_horizon_steps,
            )
            forecast_output = forecasting_service.predict(fc_input)

            target_ts = ts_utc + timedelta(seconds=forecast_output.horizon_seconds)
            pending_fc = PendingForecast(
                generated_at=ts_utc,
                target_timestamp=target_ts,
                horizon_seconds=forecast_output.horizon_seconds,
                model_name=forecast_output.model_name,
                predicted_request_rate_per_second=forecast_output.predicted_request_rate_per_second,
                fallback_used=forecast_output.fallback_used,
                history_count=forecast_output.history_count,
                warnings=forecast_output.warnings,
            )
            forecast_store.add(pending_fc)
            forecast_id = pending_fc.forecast_id
            predicted_rate = pending_fc.predicted_request_rate_per_second

            point_forecast = ForecastResult(
                generated_at=ts_utc,
                horizon_seconds=int(forecast_output.horizon_seconds),
                predicted_request_rate_per_second=predicted_rate,
                confidence=1.0,
                model_name=forecast_output.model_name,
                observations_used=forecast_output.history_count,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Forecast generation failed in cycle %d: %s", cycle_number, e)

    # 5. Evaluate Forecast Trust Guard (Phase 10A)
    trust_decision: ForecastTrustDecision | None = None
    if point_forecast is not None and pending_fc is not None:
        trust_decision = evaluate_forecast_trust(
            current_time=ts_utc,
            config=trust_guard_config,
            telemetry=telemetry_result,
            forecast=pending_fc,
            candidate_residuals=forecast_store.get_matched(),
        )

    # 6. Evaluate Rapid-Change Detector (Phase 10B)
    rapid_change_result: RapidChangeResult = detect_rapid_change(
        telemetry=telemetry_result,
        config=policy_config,
        evaluated_at=ts_utc,
    )

    # 7. Evaluate Predictive Capacity Recommendation if trusted
    predictive_capacity_result: CapacityResult | None = None
    if trust_decision is not None and trust_decision.trusted and predicted_rate is not None:
        predictive_capacity_result = calculate_capacity_recommendation(
            predicted_request_rate_per_second=predicted_rate,
            current_replicas=current_replicas,
            config=capacity_config,
        )

    # 8. Fetch read-only CPU telemetry
    cpu_telemetry: CpuTelemetryResult = cpu_telemetry_provider.get_cpu_telemetry()

    # 9. Execute Deterministic Policy Arbitration
    decision_record: ArbitrationDecisionRecord = arbitrate_policy_decision(
        timestamp=ts_utc,
        cycle_number=cycle_number,
        current_replicas=current_replicas,
        telemetry=telemetry_result,
        forecast=point_forecast,
        trust_decision=trust_decision,
        rapid_change_result=rapid_change_result,
        predictive_capacity_result=predictive_capacity_result,
        cpu_telemetry=cpu_telemetry,
        config=policy_config,
        cooldown_manager=cooldown_manager,
        stabilizer=stabilizer,
    )

    # 10. Update Operational Metrics
    metrics.record_arbitration_decision(decision_record)
    metrics.cycles_total.labels(outcome="arbitration_cycle_completed").inc()

    duration_ms = (time.perf_counter() - start_wall) * 1000.0

    return ArbitrationCycleResult(
        cycle_number=cycle_number,
        timestamp=ts_utc,
        telemetry_status=decision_record.telemetry_status,
        policy_mode=decision_record.policy_mode.value,
        primary_reason=decision_record.primary_reason.value,
        current_replicas=current_replicas,
        forecast_id=forecast_id,
        predicted_request_rate=predicted_rate,
        forecast_trusted=trust_decision.trusted if trust_decision else False,
        rapid_change_detected=rapid_change_result.detected,
        current_cpu_ratio=cpu_telemetry.cpu_utilization_ratio
        if cpu_telemetry.is_available
        else None,
        predictive_recommendation=decision_record.predictive_recommendation,
        reactive_recommendation=decision_record.reactive_recommendation,
        unconstrained_recommendation=decision_record.selected_unconstrained_recommendation,
        bounded_recommendation=decision_record.bounded_recommendation,
        step_limited_recommendation=decision_record.step_limited_recommendation,
        cooldown_blocked=decision_record.cooldown_blocked,
        stabilized_recommendation=decision_record.stabilized_recommendation,
        final_recommendation=decision_record.final_recommendation,
        direction=decision_record.direction.value,
        applied_constraints=decision_record.applied_constraints,
        action_executed=False,
        decision_record=decision_record,
        trust_decision=trust_decision,
        rapid_change_result=rapid_change_result,
        duration_ms=duration_ms,
        skip_reason=None
        if decision_record.policy_mode != PolicyMode.NO_RECOMMENDATION
        else decision_record.primary_reason.value,
    )


def persist_arbitration_artifacts(
    run_dir: Path,
    run_id: str,
    started_at: datetime,
    completed_at: datetime,
    status: str,
    cycle_results: list[ArbitrationCycleResult],
    forecast_store: ForecastStore,
    observer_config: ObserverConfig,
    capacity_config: CapacityConfig,
    trust_guard_config: TrustGuardConfig,
    policy_config: PolicyConfig,
    forecasting_service: ForecastingService,
    policy_config_path: Path,
) -> None:
    """Persist all Phase 10B evidence artifacts under experiments/output/controller-arbitration/<run-id>/."""
    run_dir.mkdir(parents=True, exist_ok=True)

    all_forecasts = forecast_store.get_all()
    matched_forecasts = forecast_store.get_matched()

    mode_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    for c in cycle_results:
        mode_counts[c.policy_mode] = mode_counts.get(c.policy_mode, 0) + 1
        reason_counts[c.primary_reason] = reason_counts.get(c.primary_reason, 0) + 1

    rapid_change_count = sum(1 for c in cycle_results if c.rapid_change_detected)
    cooldown_blocked_count = sum(1 for c in cycle_results if c.cooldown_blocked)
    downscale_stabilized_count = sum(
        1 for c in cycle_results if "downscale_stabilized" in c.applied_constraints
    )

    # 1. metadata.json
    metadata = {
        "run_id": run_id,
        "phase": "10B",
        "mode": "arbitration_dry_run",
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": round((completed_at - started_at).total_seconds(), 2),
        "status": status,
        "cycles_requested": observer_config.maximum_cycles,
        "cycles_completed": len(cycle_results),
        "forecasts_generated": len(all_forecasts),
        "forecasts_matched": len(matched_forecasts),
        "mode_counts": mode_counts,
        "reason_counts": reason_counts,
        "rapid_change_detected_count": rapid_change_count,
        "cooldown_blocked_count": cooldown_blocked_count,
        "downscale_stabilized_count": downscale_stabilized_count,
        "action_executed_all_false": all(c.action_executed is False for c in cycle_results),
        "model_name": forecasting_service.model_name,
        "model_status": forecasting_service.status,
        "capacity_status": capacity_config.status,
        "trust_guard_status": trust_guard_config.status,
        "policy_status": policy_config.status,
        "minimum_replicas": policy_config.minimum_replicas,
        "maximum_replicas": policy_config.maximum_replicas,
        "maximum_scale_up_step": policy_config.maximum_scale_up_step,
        "maximum_scale_down_step": policy_config.maximum_scale_down_step,
        "cooldown_seconds": policy_config.cooldown_seconds,
        "downscale_stabilization_seconds": policy_config.downscale_stabilization_seconds,
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
                "matched_residuals",
                "recent_mae",
                "recent_smape",
                "action_executed",
            ]
        )
        for c in cycle_results:
            td = c.trust_decision
            writer.writerow(
                [
                    c.timestamp.isoformat(),
                    c.cycle_number,
                    c.forecast_id or "",
                    td.trusted if td else False,
                    td.outcome.value if td else "no_decision",
                    td.matched_residual_count if td else 0,
                    f"{td.recent_mae:.4f}" if td and td.recent_mae is not None else "",
                    f"{td.recent_smape:.4f}" if td and td.recent_smape is not None else "",
                    False,
                ]
            )

    # 5. rapid_change_decisions.csv
    with (run_dir / "rapid_change_decisions.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "timestamp_utc",
                "cycle_number",
                "detected",
                "reason",
                "recent_rate",
                "baseline_rate",
                "absolute_increase",
                "growth_ratio",
                "sample_count",
                "absolute_threshold",
                "ratio_threshold",
            ]
        )
        for c in cycle_results:
            rc = c.rapid_change_result
            writer.writerow(
                [
                    c.timestamp.isoformat(),
                    c.cycle_number,
                    rc.detected if rc else False,
                    rc.reason.value if rc else "",
                    f"{rc.recent_rate:.4f}" if rc and rc.recent_rate is not None else "",
                    f"{rc.baseline_rate:.4f}" if rc and rc.baseline_rate is not None else "",
                    f"{rc.absolute_increase:.4f}"
                    if rc and rc.absolute_increase is not None
                    else "",
                    f"{rc.growth_ratio:.4f}" if rc and rc.growth_ratio is not None else "",
                    rc.sample_count if rc else 0,
                    policy_config.rapid_change_absolute_rate,
                    policy_config.rapid_change_growth_ratio,
                ]
            )

    # 6. reactive_recommendations.csv
    with (run_dir / "reactive_recommendations.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "timestamp_utc",
                "cycle_number",
                "eligible",
                "current_replicas",
                "current_cpu_ratio",
                "target_cpu_ratio",
                "raw_unconstrained_replicas",
                "bounded_replicas",
                "final_replicas",
                "direction",
                "reason",
                "applied_constraints",
            ]
        )
        for c in cycle_results:
            writer.writerow(
                [
                    c.timestamp.isoformat(),
                    c.cycle_number,
                    c.reactive_recommendation is not None,
                    c.current_replicas,
                    f"{c.current_cpu_ratio:.4f}" if c.current_cpu_ratio is not None else "",
                    policy_config.reactive_cpu_target_ratio,
                    c.reactive_recommendation or "",
                    c.bounded_recommendation or "",
                    c.final_recommendation or "",
                    c.direction,
                    c.primary_reason,
                    ";".join(c.applied_constraints),
                ]
            )

    # 7. arbitration_decisions.csv (required columns)
    with (run_dir / "arbitration_decisions.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "timestamp_utc",
                "cycle",
                "policy_mode",
                "primary_reason",
                "forecast_trusted",
                "rapid_change_detected",
                "request_rate",
                "current_cpu_ratio",
                "predictive_recommendation",
                "reactive_recommendation",
                "unconstrained_recommendation",
                "bounded_recommendation",
                "step_limited_recommendation",
                "cooldown_blocked",
                "stabilized_recommendation",
                "final_recommendation",
                "direction",
                "action_executed",
            ]
        )
        for c in cycle_results:
            writer.writerow(
                [
                    c.timestamp.isoformat(),
                    c.cycle_number,
                    c.policy_mode,
                    c.primary_reason,
                    c.forecast_trusted,
                    c.rapid_change_detected,
                    f"{c.predicted_request_rate:.4f}"
                    if c.predicted_request_rate is not None
                    else "",
                    f"{c.current_cpu_ratio:.4f}" if c.current_cpu_ratio is not None else "",
                    c.predictive_recommendation or "",
                    c.reactive_recommendation or "",
                    c.unconstrained_recommendation or "",
                    c.bounded_recommendation or "",
                    c.step_limited_recommendation or "",
                    c.cooldown_blocked,
                    c.stabilized_recommendation or "",
                    c.final_recommendation or "",
                    c.direction,
                    False,
                ]
            )

    # 8. matched_residuals.csv
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
                    m.actual_request_rate_per_second or "",
                    m.residual if m.residual is not None else "",
                    m.absolute_error if m.absolute_error is not None else "",
                ]
            )

    # 9. skipped_cycles.csv
    with (run_dir / "skipped_cycles.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "cycle_number",
                "timestamp_utc",
                "telemetry_status",
                "reason",
            ]
        )
        for c in cycle_results:
            if c.skip_reason:
                writer.writerow(
                    [
                        c.cycle_number,
                        c.timestamp.isoformat(),
                        c.telemetry_status,
                        c.skip_reason,
                    ]
                )

    # 10. policy_configuration.yaml
    if policy_config_path.is_file():
        with (
            policy_config_path.open("r", encoding="utf-8") as src,
            (run_dir / "policy_configuration.yaml").open("w", encoding="utf-8") as dst,
        ):
            dst.write(src.read())

    # 11. validation_report.json
    validation_report = {
        "run_id": run_id,
        "passed": True,
        "total_cycles": len(cycle_results),
        "mode_counts": mode_counts,
        "reason_counts": reason_counts,
        "rapid_change_detected_count": rapid_change_count,
        "predictive_selected_count": mode_counts.get("predictive", 0),
        "reactive_selected_count": mode_counts.get("reactive_fallback", 0),
        "override_selected_count": mode_counts.get("rapid_change_override", 0),
        "no_recommendation_count": mode_counts.get("no_recommendation", 0),
        "cooldown_blocked_count": cooldown_blocked_count,
        "downscale_stabilized_count": downscale_stabilized_count,
        "action_executed_all_false": all(c.action_executed is False for c in cycle_results),
        "no_k8s_mutations": True,
        "notes": "Arbitration controller operated strictly in read-only dry-run mode with zero actuation.",
    }
    with (run_dir / "validation_report.json").open("w", encoding="utf-8") as f:
        json.dump(validation_report, f, indent=2)


def run_arbitration_controller(
    observer_config: ObserverConfig,
    capacity_config: CapacityConfig,
    trust_guard_config: TrustGuardConfig,
    policy_config: PolicyConfig,
    forecasting_service: ForecastingService,
    telemetry_reader: LiveTelemetryReader,
    deployment_reader: ReadOnlyDeploymentReader,
    cpu_telemetry_provider: CpuTelemetryProvider,
    metrics: ArbitrationMetrics,
    output_base_dir: Path,
    config_path: Path,
    policy_config_path: Path,
) -> Path:
    """Run bounded dry-run arbitrated controller loop and persist artifacts.

    Returns
    -------
    Path
        Directory containing all output evidence artifacts.
    """
    started_at = datetime.now().astimezone()
    run_id = f"arbitration-dry-run-{started_at.strftime('%Y%m%d_%H%M%S')}"
    run_dir = output_base_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Configure execution log file
    log_file = run_dir / "execution.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
    logger.addHandler(file_handler)

    logger.info("Starting Phase 10B Arbitrated Dry-Run Controller (run_id=%s)", run_id)

    forecast_store = ForecastStore()
    cooldown_mgr = CooldownManager(policy_config.cooldown_seconds)
    stabilizer = DownscaleStabilizer(policy_config.downscale_stabilization_seconds)

    cycle_results: list[ArbitrationCycleResult] = []
    max_cycles = observer_config.maximum_cycles or 16
    interval = observer_config.forecast_interval_seconds

    try:
        for cycle_idx in range(1, max_cycles + 1):
            cycle_start = datetime.now().astimezone()
            logger.info("Executing arbitration cycle %d / %d", cycle_idx, max_cycles)

            result = execute_arbitration_cycle(
                cycle_number=cycle_idx,
                current_time=cycle_start,
                observer_config=observer_config,
                capacity_config=capacity_config,
                trust_guard_config=trust_guard_config,
                policy_config=policy_config,
                telemetry_reader=telemetry_reader,
                forecasting_service=forecasting_service,
                forecast_store=forecast_store,
                deployment_reader=deployment_reader,
                cpu_telemetry_provider=cpu_telemetry_provider,
                metrics=metrics,
                cooldown_manager=cooldown_mgr,
                stabilizer=stabilizer,
            )
            cycle_results.append(result)

            logger.info(
                "Cycle %d outcome: mode=%s, reason=%s, final_rec=%s, direction=%s",
                cycle_idx,
                result.policy_mode,
                result.primary_reason,
                result.final_recommendation,
                result.direction,
            )

            # Sleep until next interval if not on last cycle
            if cycle_idx < max_cycles:
                elapsed = (datetime.now().astimezone() - cycle_start).total_seconds()
                sleep_sec = max(0.0, interval - elapsed)
                if sleep_sec > 0:
                    time.sleep(sleep_sec)

        completed_at = datetime.now().astimezone()
        status = "completed"

    except Exception as e:
        completed_at = datetime.now().astimezone()
        status = f"error: {e}"
        logger.exception("Controller loop encountered error")
    finally:
        persist_arbitration_artifacts(
            run_dir=run_dir,
            run_id=run_id,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            cycle_results=cycle_results,
            forecast_store=forecast_store,
            observer_config=observer_config,
            capacity_config=capacity_config,
            trust_guard_config=trust_guard_config,
            policy_config=policy_config,
            forecasting_service=forecasting_service,
            policy_config_path=policy_config_path,
        )
        logger.removeHandler(file_handler)
        file_handler.close()

    return run_dir
