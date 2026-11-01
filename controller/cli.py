"""Command-line interface for KubePredict observe-only controller (Phase 9A)."""

import argparse
import csv
import hashlib
import json
import logging
import os
import signal
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from prometheus_client import CollectorRegistry

from controller.arbitration import arbitrate_policy_decision
from controller.arbitration_dry_run import (
    ArbitrationCycleResult,
    execute_arbitration_cycle,
    persist_arbitration_artifacts,
)
from controller.audit import run_write_capability_audit
from controller.capacity import calculate_capacity_recommendation
from controller.capacity_config import CapacityConfig, load_capacity_config
from controller.config import ObserverConfig, load_observer_config
from controller.cooldown import CooldownManager
from controller.cpu_telemetry import (
    CpuTelemetryResult,
    ReadOnlyCpuTelemetryReader,
)
from controller.dry_run import DryRunCycleResult, execute_dry_run_cycle
from controller.forecast_store import ForecastStore, PendingForecast
from controller.guarded_dry_run import (
    GuardedDryRunCycleResult,
    execute_guarded_dry_run_cycle,
    write_guarded_dry_run_artifacts,
)
from controller.k8s_reader import ReadOnlyDeploymentReader
from controller.metrics import (
    ArbitrationMetrics,
    DryRunMetrics,
    GuardedDryRunMetrics,
    MetricsServer,
    ObserverMetrics,
)
from controller.observer import ObserverCycleResult, execute_observer_cycle
from controller.policy_config import PolicyConfig, load_policy_config
from controller.rapid_change import detect_rapid_change
from controller.stabilization import DownscaleStabilizer
from controller.telemetry import LiveTelemetryReader, TelemetryResult
from controller.trust_config import load_trust_guard_config
from controller.trust_guard import evaluate_forecast_trust
from forecasting.service import ForecastingService
from shared.enums import DecisionReason, PolicyMode, TrustOutcome
from shared.models import ForecastTrustDecision

logger = logging.getLogger("kubepredict.controller")


def setup_logger(output_dir: Path | None = None) -> None:
    """Configure structured console and optional file logging."""
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if output_dir is not None:
        log_file = output_dir / "execution.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)


def compute_file_hash(path: Path) -> str:
    """Compute SHA-256 hash of a file if it exists."""
    if not path.exists():
        return "absent"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_mode(
    config_path: str,
    frozen_forecaster_path: str,
    capacity_config_path: str = "controller/config/capacity.yaml",
) -> int:
    """Validate observer configuration, frozen forecaster, and capacity model."""
    print("======================================================================")
    print("PHASE 9B CONTROLLER - CONFIGURATION & CAPACITY MODEL VALIDATION")
    print("======================================================================")

    c_path = Path(config_path)
    f_path = Path(frozen_forecaster_path)
    cap_path = Path(capacity_config_path)

    errors: list[str] = []

    # 1. Validate Observer Config
    if not c_path.exists():
        print(f"FAILED: Observer configuration file not found at {c_path}", file=sys.stderr)
        return 1

    try:
        cfg = ObserverConfig.from_yaml_file(c_path)
        print(f"[OK] Controller configuration valid: mode={cfg.mode}")
        print(f"     Prometheus URL: {cfg.prometheus_url}")
        print(f"     Fixed Query:    {cfg.query}")
        print(f"     Sampling Step:  {cfg.sampling_interval_seconds}s")
        print(f"     History Window: {cfg.history_window_seconds}s")
        print(f"     Horizon:        {cfg.forecast_horizon_steps} step(s)")
        print(f"     Max Cycles:     {cfg.maximum_cycles}")
    except Exception as e:  # noqa: BLE001  # noqa: BLE001
        errors.append(f"ObserverConfig validation failed: {e}")
        print(f"[FAIL] ObserverConfig: {e}", file=sys.stderr)

    # 2. Validate Frozen Forecaster
    if not f_path.exists():
        errors.append(f"Frozen forecaster file not found at {f_path}")
        print(f"[FAIL] Frozen forecaster config not found at {f_path}", file=sys.stderr)
    else:
        try:
            svc = ForecastingService.from_config_file(f_path)
            if svc.status != "offline_selected":
                errors.append(
                    f"ForecastingService status must be 'offline_selected', got '{svc.status}'"
                )
            print(f"[OK] Frozen forecaster valid: model={svc.model_name} status={svc.status}")
        except Exception as e:  # noqa: BLE001  # noqa: BLE001
            errors.append(f"ForecastingService loading failed: {e}")
            print(f"[FAIL] Frozen forecaster: {e}", file=sys.stderr)

    # 3. Validate Capacity Configuration (Phase 9B)
    if not cap_path.exists():
        errors.append(f"Capacity configuration file not found at {cap_path}")
        print(f"[FAIL] CapacityConfig not found at {cap_path}", file=sys.stderr)
    else:
        try:
            cap_cfg = CapacityConfig.from_yaml_file(cap_path)
            print(f"[OK] Capacity configuration valid: status={cap_cfg.status}")
            print(f"     Base Capacity:  {cap_cfg.capacity_requests_per_second_per_replica} req/s")
            print(f"     Target Util:    {cap_cfg.target_utilization_ratio}")
            print(f"     Safety Margin:  {cap_cfg.safety_margin_ratio}")
            print(f"     Effective Cap:  {cap_cfg.effective_capacity_per_replica} req/s")
            print(f"     Replica Bounds: [{cap_cfg.minimum_replicas}, {cap_cfg.maximum_replicas}]")
            print(
                f"     Step Limits:    +{cap_cfg.maximum_scale_up_step} / -{cap_cfg.maximum_scale_down_step}"
            )
            print(f"     Evidence Ref:   {cap_cfg.capacity_evidence_reference}")
        except Exception as e:  # noqa: BLE001  # noqa: BLE001
            errors.append(f"CapacityConfig loading failed: {e}")
            print(f"[FAIL] CapacityConfig: {e}", file=sys.stderr)

    # 3. Static Write-Capability Audit
    report = run_write_capability_audit()
    if not report.passed:
        errors.append(f"Static write audit found {len(report.violations)} violations")
        print(f"[FAIL] Static write audit: {len(report.violations)} violation(s)", file=sys.stderr)
        for v in report.violations:
            print(
                f"       {v.file_path}:{v.line_number} [{v.rule}] {v.explanation}", file=sys.stderr
            )
    else:
        print(
            f"[OK] Static write audit passed ({len(report.files_scanned)} files scanned, 0 write calls)"
        )

    if errors:
        print("\nVALIDATION FAILED with errors:")
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("\n[SUCCESS] Observer configuration, frozen model, and write audit passed.")
    return 0


def audit_source_mode() -> int:
    """Execute static write-capability audit on controller modules."""
    print("======================================================================")
    print("PHASE 9A STATIC WRITE-CAPABILITY AUDIT")
    print("======================================================================")
    report = run_write_capability_audit()
    print(f"Files Scanned: {len(report.files_scanned)}")
    for f in report.files_scanned:
        print(f"  - {f}")

    if not report.passed:
        print(
            f"\n[FAIL] Found {len(report.violations)} write-capability violations:", file=sys.stderr
        )
        for v in report.violations:
            print(f"  {v.file_path}:{v.line_number} [{v.rule}] {v.explanation}", file=sys.stderr)
            if v.code_snippet:
                print(f"    > {v.code_snippet}", file=sys.stderr)
        return 1

    print("\n[PASS] No Kubernetes client imports or write-capable calls found in controller code.")
    return 0


def observe_mode(
    config_path: str,
    frozen_forecaster_path: str,
    output_root: str,
    run_id: str | None = None,
    max_cycles_override: int | None = None,
) -> int:
    """Execute bounded observe-only controller loop."""
    c_path = Path(config_path)
    f_path = Path(frozen_forecaster_path)
    out_root = Path(output_root)

    cfg = ObserverConfig.from_yaml_file(c_path)
    if max_cycles_override is not None and max_cycles_override > 0:
        # Override cycles if explicitly requested via CLI
        cfg_dict = cfg.model_dump()
        cfg_dict["maximum_cycles"] = max_cycles_override
        cfg = ObserverConfig(**cfg_dict)

    forecasting_service = ForecastingService.from_config_file(f_path)

    # Establish unique run directory
    actual_run_id = run_id or f"observe-{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    run_dir = out_root / actual_run_id
    if run_dir.exists():
        print(f"ERROR: Run directory {run_dir} already exists. Aborting.", file=sys.stderr)
        return 1
    run_dir.mkdir(parents=True, exist_ok=False)

    setup_logger(run_dir)
    logger.info("======================================================================")
    logger.info("PHASE 9A OBSERVE-ONLY FORECASTING CONTROLLER")
    logger.info("======================================================================")
    logger.info("Run ID:          %s", actual_run_id)
    logger.info("Output Dir:      %s", run_dir)
    logger.info("Prometheus URL:  %s", cfg.prometheus_url)
    logger.info("Fixed Query:     %s", cfg.query)
    logger.info("Cycle Interval:  %.1fs", cfg.forecast_interval_seconds)
    logger.info("Max Cycles:      %d", cfg.maximum_cycles)
    logger.info("Frozen Model:    %s", forecasting_service.model_name)
    logger.info(
        "Default Horizon: %d step(s) (%.1fs)",
        cfg.forecast_horizon_steps,
        cfg.forecast_horizon_steps * cfg.sampling_interval_seconds,
    )

    # Start Metrics Server
    metrics = ObserverMetrics()
    metrics_server = MetricsServer(
        host=cfg.metrics_host, port=cfg.metrics_port, registry=metrics.registry
    )
    try:
        metrics_server.start()
    except Exception as e:  # noqa: BLE001  # noqa: BLE001
        logger.error(
            "Failed to start metrics server on %s:%d: %s", cfg.metrics_host, cfg.metrics_port, e
        )
        return 1

    telemetry_reader = LiveTelemetryReader(cfg)
    forecast_store = ForecastStore()

    cycle_results: list[ObserverCycleResult] = []
    run_status = "completed"
    interrupted = False

    def handle_sigint(signum: Any, frame: Any) -> None:
        nonlocal interrupted
        interrupted = True
        logger.warning("\nInterrupt received. Finishing current cycle and cleaning up...")

    orig_sigint = signal.signal(signal.SIGINT, handle_sigint)

    started_at = datetime.now(UTC)
    try:
        for cycle_num in range(1, cfg.maximum_cycles + 1):
            if interrupted:
                run_status = "interrupted"
                break

            now_utc = datetime.now(UTC)
            res = execute_observer_cycle(
                cycle_number=cycle_num,
                current_time=now_utc,
                config=cfg,
                telemetry_reader=telemetry_reader,
                forecasting_service=forecasting_service,
                forecast_store=forecast_store,
                metrics=metrics,
            )
            cycle_results.append(res)

            if cycle_num < cfg.maximum_cycles and not interrupted:
                time.sleep(cfg.forecast_interval_seconds)

    except Exception:
        logger.exception("Observer cycle execution failed")
        run_status = "failed"
    finally:
        completed_at = datetime.now(UTC)
        signal.signal(signal.SIGINT, orig_sigint)
        metrics_server.stop()

        # Write artifacts to output directory
        _write_run_artifacts(
            run_dir=run_dir,
            run_id=actual_run_id,
            config=cfg,
            forecasting_service=forecasting_service,
            forecast_store=forecast_store,
            cycle_results=cycle_results,
            started_at=started_at,
            completed_at=completed_at,
            status=run_status,
            config_path=c_path,
            frozen_forecaster_path=f_path,
        )

    logger.info("Observe-only run %s finished with status '%s'.", actual_run_id, run_status)
    return 0 if run_status in ("completed", "interrupted") else 1


def _write_run_artifacts(
    run_dir: Path,
    run_id: str,
    config: ObserverConfig,
    forecasting_service: ForecastingService,
    forecast_store: ForecastStore,
    cycle_results: list[ObserverCycleResult],
    started_at: datetime,
    completed_at: datetime,
    status: str,
    config_path: Path,
    frozen_forecaster_path: Path,
) -> None:
    """Persist all Phase 9A evidence artifacts outside Git."""
    # 1. metadata.json
    all_forecasts = forecast_store.get_all()
    matched_forecasts = forecast_store.get_matched()
    pending_forecasts = forecast_store.get_pending()
    expired_forecasts = forecast_store.get_expired()

    metadata = {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "status": status,
        "mode": config.mode,
        "controller_config_sha256": compute_file_hash(config_path),
        "frozen_forecaster_sha256": compute_file_hash(frozen_forecaster_path),
        "model_name": forecasting_service.model_name,
        "default_horizon_steps": config.forecast_horizon_steps,
        "default_horizon_seconds": config.forecast_horizon_steps * config.sampling_interval_seconds,
        "query": config.query,
        "cycle_interval_seconds": config.forecast_interval_seconds,
        "maximum_cycles": config.maximum_cycles,
        "cycles_executed": len(cycle_results),
        "forecasts_created": len(all_forecasts),
        "forecasts_matched": len(matched_forecasts),
        "forecasts_pending": len(pending_forecasts),
        "forecasts_expired": len(expired_forecasts),
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    # 2. cycles.jsonl
    with (run_dir / "cycles.jsonl").open("w", encoding="utf-8") as f:
        for c in cycle_results:
            f.write(c.model_dump_json() + "\n")

    # 3. forecasts.csv
    with (run_dir / "forecasts.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "forecast_id",
            "generated_at",
            "target_timestamp",
            "horizon_seconds",
            "model_name",
            "predicted_request_rate_per_second",
            "fallback_used",
            "history_count",
            "actual_request_rate_per_second",
            "residual",
            "absolute_error",
            "status",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for fc in all_forecasts:
            d = fc.model_dump()
            d["generated_at"] = fc.generated_at.isoformat()
            d["target_timestamp"] = fc.target_timestamp.isoformat()
            d.pop("warnings", None)
            writer.writerow(d)

    # 4. matched_residuals.csv
    with (run_dir / "matched_residuals.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "forecast_id",
            "generated_at",
            "target_timestamp",
            "horizon_seconds",
            "predicted_request_rate_per_second",
            "actual_request_rate_per_second",
            "residual",
            "absolute_error",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for fc in matched_forecasts:
            writer.writerow(
                {
                    "forecast_id": fc.forecast_id,
                    "generated_at": fc.generated_at.isoformat(),
                    "target_timestamp": fc.target_timestamp.isoformat(),
                    "horizon_seconds": fc.horizon_seconds,
                    "predicted_request_rate_per_second": fc.predicted_request_rate_per_second,
                    "actual_request_rate_per_second": fc.actual_request_rate_per_second,
                    "residual": fc.residual,
                    "absolute_error": fc.absolute_error,
                }
            )

    # 5. skipped_cycles.csv
    skipped_cycles = [c for c in cycle_results if c.outcome.startswith("forecast_skipped_")]
    with (run_dir / "skipped_cycles.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["cycle_number", "timestamp", "outcome", "skip_reason", "duration_ms"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for sc in skipped_cycles:
            writer.writerow(
                {
                    "cycle_number": sc.cycle_number,
                    "timestamp": sc.timestamp.isoformat(),
                    "outcome": sc.outcome,
                    "skip_reason": sc.skip_reason or "",
                    "duration_ms": sc.duration_ms,
                }
            )

    # 6. validation_report.json
    val_report = {
        "run_id": run_id,
        "status": status,
        "all_cycles_valid": all(c.duration_ms >= 0 for c in cycle_results),
        "telemetry_freshness_enforced": True,
        "zero_kubernetes_writes": True,
        "zero_replica_recommendations": True,
        "residual_count": len(matched_forecasts),
        "mean_absolute_error": (
            round(
                sum(m.absolute_error for m in matched_forecasts if m.absolute_error is not None)
                / len(matched_forecasts),
                4,
            )
            if matched_forecasts
            else None
        ),
    }
    (run_dir / "validation_report.json").write_text(
        json.dumps(val_report, indent=2), encoding="utf-8"
    )


def dry_run_mode(
    config_path: str,
    capacity_config_path: str,
    frozen_forecaster_path: str,
    output_root: str,
    run_id: str | None = None,
    max_cycles_override: int | None = None,
) -> int:
    """Execute bounded dry-run controller loop with capacity recommendations."""
    c_path = Path(config_path)
    cap_path = Path(capacity_config_path)
    f_path = Path(frozen_forecaster_path)
    out_root = Path(output_root)

    cfg = ObserverConfig.from_yaml_file(c_path)
    if max_cycles_override is not None and max_cycles_override > 0:
        cfg_dict = cfg.model_dump()
        cfg_dict["maximum_cycles"] = max_cycles_override
        cfg = ObserverConfig(**cfg_dict)

    cap_cfg = CapacityConfig.from_yaml_file(cap_path)
    forecasting_service = ForecastingService.from_config_file(f_path)

    actual_run_id = run_id or f"dry-run-{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    run_dir = out_root / actual_run_id
    if run_dir.exists():
        print(f"ERROR: Run directory {run_dir} already exists. Aborting.", file=sys.stderr)
        return 1
    run_dir.mkdir(parents=True, exist_ok=False)

    setup_logger(run_dir)
    logger.info("======================================================================")
    logger.info("PHASE 9B DRY-RUN CAPACITY & REPLICA RECOMMENDATION CONTROLLER")
    logger.info("======================================================================")
    logger.info("Run ID:          %s", actual_run_id)
    logger.info("Output Dir:      %s", run_dir)
    logger.info("Prometheus URL:  %s", cfg.prometheus_url)
    logger.info("Fixed Query:     %s", cfg.query)
    logger.info("Cycle Interval:  %.1fs", cfg.forecast_interval_seconds)
    logger.info("Max Cycles:      %d", cfg.maximum_cycles)
    logger.info("Frozen Model:    %s", forecasting_service.model_name)
    logger.info(
        "Effective Cap:   %.4f req/s per replica",
        cap_cfg.effective_capacity_per_replica,
    )
    logger.info("Replica Bounds:  [%d, %d]", cap_cfg.minimum_replicas, cap_cfg.maximum_replicas)
    logger.info(
        "Step Limits:     +%d / -%d",
        cap_cfg.maximum_scale_up_step,
        cap_cfg.maximum_scale_down_step,
    )

    metrics = DryRunMetrics()
    metrics_server = MetricsServer(
        host=cfg.metrics_host, port=cfg.metrics_port, registry=metrics.registry
    )
    try:
        metrics_server.start()
    except Exception as e:  # noqa: BLE001  # noqa: BLE001
        logger.error(
            "Failed to start metrics server on %s:%d: %s",
            cfg.metrics_host,
            cfg.metrics_port,
            e,
        )
        return 1

    telemetry_reader = LiveTelemetryReader(cfg)
    forecast_store = ForecastStore()
    deployment_reader = ReadOnlyDeploymentReader()

    cycle_results: list[DryRunCycleResult] = []
    run_status = "completed"
    interrupted = False

    def handle_sigint(signum: Any, frame: Any) -> None:
        nonlocal interrupted
        interrupted = True
        logger.warning("\nInterrupt received. Finishing current cycle and cleaning up...")

    orig_sigint = signal.signal(signal.SIGINT, handle_sigint)

    started_at = datetime.now(UTC)
    try:
        for cycle_num in range(1, cfg.maximum_cycles + 1):
            if interrupted:
                run_status = "interrupted"
                break

            now_utc = datetime.now(UTC)
            res = execute_dry_run_cycle(
                cycle_number=cycle_num,
                current_time=now_utc,
                observer_config=cfg,
                capacity_config=cap_cfg,
                telemetry_reader=telemetry_reader,
                forecasting_service=forecasting_service,
                forecast_store=forecast_store,
                deployment_reader=deployment_reader,
                metrics=metrics,
            )
            cycle_results.append(res)

            if cycle_num < cfg.maximum_cycles and not interrupted:
                time.sleep(cfg.forecast_interval_seconds)

    except Exception:
        logger.exception("Dry-run cycle execution failed")
        run_status = "failed"
    finally:
        completed_at = datetime.now(UTC)
        signal.signal(signal.SIGINT, orig_sigint)
        metrics_server.stop()

        _write_dry_run_artifacts(
            run_dir=run_dir,
            run_id=actual_run_id,
            observer_config=cfg,
            capacity_config=cap_cfg,
            forecasting_service=forecasting_service,
            forecast_store=forecast_store,
            cycle_results=cycle_results,
            started_at=started_at,
            completed_at=completed_at,
            status=run_status,
            config_path=c_path,
            capacity_config_path=cap_path,
            frozen_forecaster_path=f_path,
        )

    logger.info("Dry-run %s finished with status '%s'.", actual_run_id, run_status)
    return 0 if run_status in ("completed", "interrupted") else 1


def _write_dry_run_artifacts(
    run_dir: Path,
    run_id: str,
    observer_config: ObserverConfig,
    capacity_config: CapacityConfig,
    forecasting_service: ForecastingService,
    forecast_store: ForecastStore,
    cycle_results: list[DryRunCycleResult],
    started_at: datetime,
    completed_at: datetime,
    status: str,
    config_path: Path,
    capacity_config_path: Path,
    frozen_forecaster_path: Path,
) -> None:
    """Persist all Phase 9B evidence artifacts outside Git."""
    all_forecasts = forecast_store.get_all()
    matched_forecasts = forecast_store.get_matched()
    pending_forecasts = forecast_store.get_pending()
    expired_forecasts = forecast_store.get_expired()
    recommendation_cycles = [c for c in cycle_results if c.decision is not None]
    skipped_cycles = [
        c for c in cycle_results if c.outcome.startswith(("forecast_skipped_", "dry_run_skipped_"))
    ]

    direction_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    constraint_counts: dict[str, int] = {}

    for c in recommendation_cycles:
        if c.direction:
            direction_counts[c.direction] = direction_counts.get(c.direction, 0) + 1
        if c.reason:
            reason_counts[c.reason] = reason_counts.get(c.reason, 0) + 1
        for cn in c.applied_constraints:
            constraint_counts[cn] = constraint_counts.get(cn, 0) + 1

    # 1. metadata.json
    metadata = {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "status": status,
        "mode": "dry_run",
        "controller_config_sha256": compute_file_hash(config_path),
        "capacity_config_sha256": compute_file_hash(capacity_config_path),
        "frozen_forecaster_sha256": compute_file_hash(frozen_forecaster_path),
        "model_name": forecasting_service.model_name,
        "default_horizon_steps": observer_config.forecast_horizon_steps,
        "default_horizon_seconds": observer_config.forecast_horizon_steps
        * observer_config.sampling_interval_seconds,
        "query": observer_config.query,
        "cycle_interval_seconds": observer_config.forecast_interval_seconds,
        "maximum_cycles": observer_config.maximum_cycles,
        "cycles_executed": len(cycle_results),
        "forecasts_created": len(all_forecasts),
        "forecasts_matched": len(matched_forecasts),
        "forecasts_pending": len(pending_forecasts),
        "forecasts_expired": len(expired_forecasts),
        "recommendations_created": len(recommendation_cycles),
        "skipped_cycles_count": len(skipped_cycles),
        "effective_capacity_per_replica": capacity_config.effective_capacity_per_replica,
        "direction_counts": direction_counts,
        "reason_counts": reason_counts,
        "constraint_counts": constraint_counts,
        "all_action_executed_false": all(c.action_executed is False for c in cycle_results),
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    # 2. cycles.jsonl
    with (run_dir / "cycles.jsonl").open("w", encoding="utf-8") as f:
        for c in cycle_results:
            f.write(c.model_dump_json() + "\n")

    # 3. forecasts.csv
    with (run_dir / "forecasts.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "forecast_id",
            "generated_at",
            "target_timestamp",
            "horizon_seconds",
            "model_name",
            "predicted_request_rate_per_second",
            "fallback_used",
            "history_count",
            "actual_request_rate_per_second",
            "residual",
            "absolute_error",
            "status",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for fc in all_forecasts:
            d = fc.model_dump()
            d["generated_at"] = fc.generated_at.isoformat()
            d["target_timestamp"] = fc.target_timestamp.isoformat()
            d.pop("warnings", None)
            writer.writerow(d)

    # 4. recommendations.csv
    with (run_dir / "recommendations.csv").open("w", newline="", encoding="utf-8") as f:
        rec_fields = [
            "timestamp_utc",
            "cycle_number",
            "forecast_id",
            "model",
            "horizon_seconds",
            "predicted_request_rate_per_second",
            "current_replicas",
            "effective_capacity_per_replica",
            "unconstrained_required_replicas",
            "proposed_replicas",
            "final_replicas",
            "direction",
            "reason",
            "applied_constraints",
            "action_executed",
        ]
        writer = csv.DictWriter(f, fieldnames=rec_fields)
        writer.writeheader()
        for c in recommendation_cycles:
            writer.writerow(
                {
                    "timestamp_utc": c.timestamp.isoformat(),
                    "cycle_number": c.cycle_number,
                    "forecast_id": c.forecast_id or "",
                    "model": forecasting_service.model_name,
                    "horizon_seconds": observer_config.forecast_horizon_steps
                    * observer_config.sampling_interval_seconds,
                    "predicted_request_rate_per_second": c.predicted_request_rate,
                    "current_replicas": c.current_replicas,
                    "effective_capacity_per_replica": c.effective_capacity_per_replica,
                    "unconstrained_required_replicas": c.unconstrained_required_replicas,
                    "proposed_replicas": c.proposed_replicas,
                    "final_replicas": c.final_replicas,
                    "direction": c.direction,
                    "reason": c.reason,
                    "applied_constraints": ";".join(c.applied_constraints),
                    "action_executed": False,
                }
            )

    # 5. matched_residuals.csv
    with (run_dir / "matched_residuals.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "forecast_id",
            "generated_at",
            "target_timestamp",
            "horizon_seconds",
            "predicted_request_rate_per_second",
            "actual_request_rate_per_second",
            "residual",
            "absolute_error",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for fc in matched_forecasts:
            writer.writerow(
                {
                    "forecast_id": fc.forecast_id,
                    "generated_at": fc.generated_at.isoformat(),
                    "target_timestamp": fc.target_timestamp.isoformat(),
                    "horizon_seconds": fc.horizon_seconds,
                    "predicted_request_rate_per_second": fc.predicted_request_rate_per_second,
                    "actual_request_rate_per_second": fc.actual_request_rate_per_second,
                    "residual": fc.residual,
                    "absolute_error": fc.absolute_error,
                }
            )

    # 6. skipped_cycles.csv
    with (run_dir / "skipped_cycles.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["cycle_number", "timestamp", "outcome", "skip_reason", "duration_ms"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for sc in skipped_cycles:
            writer.writerow(
                {
                    "cycle_number": sc.cycle_number,
                    "timestamp": sc.timestamp.isoformat(),
                    "outcome": sc.outcome,
                    "skip_reason": sc.skip_reason or "",
                    "duration_ms": sc.duration_ms,
                }
            )

    # 7. capacity_configuration.yaml
    if capacity_config_path.exists():
        (run_dir / "capacity_configuration.yaml").write_text(
            capacity_config_path.read_text(encoding="utf-8"), encoding="utf-8"
        )

    # 8. validation_report.json
    val_report = {
        "run_id": run_id,
        "status": status,
        "all_cycles_valid": all(c.duration_ms >= 0 for c in cycle_results),
        "all_action_executed_false": all(c.action_executed is False for c in cycle_results),
        "telemetry_freshness_enforced": True,
        "zero_kubernetes_writes": True,
        "zero_kubernetes_mutations": True,
        "residual_count": len(matched_forecasts),
        "recommendation_count": len(recommendation_cycles),
        "skipped_cycles_count": len(skipped_cycles),
        "effective_capacity_per_replica": capacity_config.effective_capacity_per_replica,
        "direction_distribution": direction_counts,
        "reason_distribution": reason_counts,
        "constraint_distribution": constraint_counts,
        "mean_absolute_error": (
            round(
                sum(m.absolute_error for m in matched_forecasts if m.absolute_error is not None)
                / len(matched_forecasts),
                4,
            )
            if matched_forecasts
            else None
        ),
    }
    (run_dir / "validation_report.json").write_text(
        json.dumps(val_report, indent=2), encoding="utf-8"
    )


def validate_trust_guard_mode(
    config_path: str = "config/observer.yaml",
    frozen_forecaster_path: str = "forecasting/config/final_forecaster.yaml",
    capacity_config_path: str = "controller/config/capacity.yaml",
    trust_guard_config_path: str = "controller/config/trust_guard.yaml",
) -> int:
    """Validate observer config, frozen forecaster, capacity model, trust guard, and run deterministic examples."""
    print("======================================================================")
    print("PHASE 10A FORECAST TRUST GUARD - CONFIGURATION & DETERMINISTIC VALIDATION")
    print("======================================================================")

    c_path = Path(config_path)
    f_path = Path(frozen_forecaster_path)
    cap_path = Path(capacity_config_path)
    tg_path = Path(trust_guard_config_path)

    errors: list[str] = []

    # 1. Validate Observer Config
    if not c_path.exists():
        print(f"[FAIL] Observer configuration not found: {c_path}", file=sys.stderr)
        errors.append(f"ObserverConfig not found: {c_path}")
    else:
        try:
            cfg = ObserverConfig.from_yaml_file(c_path)
            print(
                f"[OK] Observer config valid: mode={cfg.mode}, sampling={cfg.sampling_interval_seconds}s, horizon={cfg.forecast_horizon_steps}"
            )
        except Exception as e:  # noqa: BLE001  # noqa: BLE001
            errors.append(f"ObserverConfig: {e}")
            print(f"[FAIL] ObserverConfig: {e}", file=sys.stderr)

    # 2. Validate Frozen Forecaster
    if not f_path.exists():
        errors.append(f"Frozen forecaster not found: {f_path}")
        print(f"[FAIL] Frozen forecaster not found: {f_path}", file=sys.stderr)
    else:
        try:
            svc = ForecastingService.from_config_file(f_path)
            print(f"[OK] Frozen forecaster valid: model={svc.model_name}, status={svc.status}")
        except Exception as e:  # noqa: BLE001  # noqa: BLE001
            errors.append(f"ForecastingService: {e}")
            print(f"[FAIL] ForecastingService: {e}", file=sys.stderr)

    # 3. Validate Capacity Config
    if not cap_path.exists():
        errors.append(f"CapacityConfig not found: {cap_path}")
        print(f"[FAIL] CapacityConfig not found: {cap_path}", file=sys.stderr)
    else:
        try:
            cap_cfg = CapacityConfig.from_yaml_file(cap_path)
            print(
                f"[OK] Capacity configuration valid: status={cap_cfg.status}, eff_cap={cap_cfg.effective_capacity_per_replica} req/s"
            )
        except Exception as e:  # noqa: BLE001  # noqa: BLE001
            errors.append(f"CapacityConfig: {e}")
            print(f"[FAIL] CapacityConfig: {e}", file=sys.stderr)

    # 4. Validate Trust Guard Config
    if not tg_path.exists():
        errors.append(f"TrustGuardConfig not found: {tg_path}")
        print(f"[FAIL] TrustGuardConfig not found: {tg_path}", file=sys.stderr)
    else:
        try:
            tg_cfg = load_trust_guard_config(tg_path)
            print(f"[OK] Trust Guard configuration valid: status={tg_cfg.status}")
            print(f"     Enabled:               {tg_cfg.enabled}")
            print(f"     Evidence Window:       {tg_cfg.evidence_window_seconds}s")
            print(f"     Min Residuals:         {tg_cfg.minimum_matched_residuals}")
            print(f"     Max Residual Age:      {tg_cfg.residual_max_age_seconds}s")
            print(
                f"     Absolute Threshold:    {tg_cfg.absolute_error_threshold} req/s (primary={tg_cfg.primary_error_metric.value})"
            )
            print(f"     Relative Threshold:    {tg_cfg.relative_error_threshold}% sMAPE")
            print(f"     Threshold Source:      {tg_cfg.threshold_source}")
            print(f"     Evidence Reference:    {tg_cfg.threshold_evidence_reference}")
        except Exception as e:  # noqa: BLE001  # noqa: BLE001
            errors.append(f"TrustGuardConfig: {e}")
            print(f"[FAIL] TrustGuardConfig: {e}", file=sys.stderr)

    # 5. Run Deterministic Known-History Examples
    if not errors:
        print("\n--- DETERMINISTIC TRUST GUARD EXAMPLES ---")
        now = datetime.now(UTC)
        tg_cfg = load_trust_guard_config(tg_path)

        valid_telem = TelemetryResult(
            query_started_at=now - timedelta(seconds=2),
            query_completed_at=now,
            expected_interval_seconds=15.0,
            timestamps=[now - timedelta(seconds=15)],
            values=[10.0],
            observation_count=1,
            latest_age_seconds=15.0,
            is_fresh=True,
            is_sufficient=True,
            status="available_fresh",
        )
        valid_fc = PendingForecast(
            forecast_id="test-fc-01",
            generated_at=now - timedelta(seconds=15),
            target_timestamp=now,
            horizon_seconds=15.0,
            model_name="persistence",
            predicted_request_rate_per_second=10.0,
            history_count=1,
        )

        # Example 1: Insufficient residual history
        d1 = evaluate_forecast_trust(now, tg_cfg, valid_telem, valid_fc, [])
        assert d1.trusted is False and d1.outcome == TrustOutcome.INSUFFICIENT_RESIDUAL_HISTORY
        print(f"[PASS] Insufficient residuals: trusted={d1.trusted}, outcome={d1.outcome.value}")

        # Example 2: Sufficient residuals with low error
        residuals_low = [
            PendingForecast(
                forecast_id=f"rec-l-{i}",
                generated_at=now - timedelta(seconds=15 * (i + 1)),
                target_timestamp=now - timedelta(seconds=15 * i),
                horizon_seconds=15.0,
                model_name="persistence",
                predicted_request_rate_per_second=10.0,
                actual_request_rate_per_second=10.2,
                residual=0.2,
                absolute_error=0.2,
                status="matched",
                history_count=1,
            )
            for i in range(1, tg_cfg.minimum_matched_residuals + 1)
        ]
        d2 = evaluate_forecast_trust(now, tg_cfg, valid_telem, valid_fc, residuals_low)
        assert d2.trusted is True and d2.outcome == TrustOutcome.TRUSTED
        print(
            f"[PASS] Low error residuals: trusted={d2.trusted}, outcome={d2.outcome.value}, MAE={d2.recent_mae}, sMAPE={d2.recent_smape}%"
        )

        # Example 3: High error exceeding threshold
        residuals_high = [
            PendingForecast(
                forecast_id=f"rec-h-{i}",
                generated_at=now - timedelta(seconds=15 * (i + 1)),
                target_timestamp=now - timedelta(seconds=15 * i),
                horizon_seconds=15.0,
                model_name="persistence",
                predicted_request_rate_per_second=10.0,
                actual_request_rate_per_second=35.0,
                residual=25.0,
                absolute_error=25.0,
                status="matched",
                history_count=1,
            )
            for i in range(1, tg_cfg.minimum_matched_residuals + 1)
        ]
        d3 = evaluate_forecast_trust(now, tg_cfg, valid_telem, valid_fc, residuals_high)
        assert d3.trusted is False and d3.outcome in (
            TrustOutcome.ABSOLUTE_ERROR_TOO_HIGH,
            TrustOutcome.ERROR_THRESHOLD_EXCEEDED,
        )
        print(
            f"[PASS] High error residuals: trusted={d3.trusted}, outcome={d3.outcome.value}, MAE={d3.recent_mae}"
        )

        # Example 4: Stale residual evidence
        residuals_stale = [
            PendingForecast(
                forecast_id=f"rec-s-{i}",
                generated_at=now - timedelta(seconds=tg_cfg.residual_max_age_seconds + 30 + 15 * i),
                target_timestamp=now
                - timedelta(seconds=tg_cfg.residual_max_age_seconds + 15 + 15 * i),
                horizon_seconds=15.0,
                model_name="persistence",
                predicted_request_rate_per_second=10.0,
                actual_request_rate_per_second=10.1,
                residual=0.1,
                absolute_error=0.1,
                status="matched",
                history_count=1,
            )
            for i in range(tg_cfg.minimum_matched_residuals)
        ]
        d4 = evaluate_forecast_trust(now, tg_cfg, valid_telem, valid_fc, residuals_stale)
        assert d4.trusted is False and d4.outcome == TrustOutcome.RESIDUAL_HISTORY_STALE
        print(f"[PASS] Stale residual history: trusted={d4.trusted}, outcome={d4.outcome.value}")

        # Example 5: Zero traffic handling
        residuals_zero = [
            PendingForecast(
                forecast_id=f"rec-z-{i}",
                generated_at=now - timedelta(seconds=15 * (i + 1)),
                target_timestamp=now - timedelta(seconds=15 * i),
                horizon_seconds=15.0,
                model_name="persistence",
                predicted_request_rate_per_second=0.0,
                actual_request_rate_per_second=0.0,
                residual=0.0,
                absolute_error=0.0,
                status="matched",
                history_count=1,
            )
            for i in range(1, tg_cfg.minimum_matched_residuals + 1)
        ]
        fc_zero = PendingForecast(
            forecast_id="test-fc-zero",
            generated_at=now - timedelta(seconds=15),
            target_timestamp=now,
            horizon_seconds=15.0,
            model_name="persistence",
            predicted_request_rate_per_second=0.0,
            history_count=1,
        )
        d5 = evaluate_forecast_trust(now, tg_cfg, valid_telem, fc_zero, residuals_zero)
        assert (
            d5.trusted is True
            and d5.outcome == TrustOutcome.TRUSTED
            and d5.recent_mae == 0.0
            and d5.recent_smape == 0.0
        )
        print(
            f"[PASS] Zero-traffic handling: trusted={d5.trusted}, outcome={d5.outcome.value}, MAE={d5.recent_mae}, sMAPE={d5.recent_smape}%"
        )

    # 6. Static write audit
    report = run_write_capability_audit()
    if not report.passed:
        errors.append(f"Static write audit found {len(report.violations)} violations")
        print(f"[FAIL] Static write audit: {len(report.violations)} violation(s)", file=sys.stderr)
    else:
        print(
            f"[OK] Static write audit passed ({len(report.files_scanned)} files scanned, 0 write calls)"
        )

    if errors:
        print("\nVALIDATION FAILED with errors:")
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(
        "\n[SUCCESS] Forecast Trust Guard configuration and deterministic logic validated successfully."
    )
    return 0


def guarded_dry_run_mode(
    config_path: str,
    capacity_config_path: str,
    trust_guard_config_path: str,
    frozen_forecaster_path: str,
    output_root: str,
    run_id: str | None = None,
    max_cycles_override: int | None = None,
) -> int:
    """Execute bounded guarded dry-run controller loop with Forecast Trust Guard."""
    c_path = Path(config_path)
    cap_path = Path(capacity_config_path)
    tg_path = Path(trust_guard_config_path)
    f_path = Path(frozen_forecaster_path)
    out_root = Path(output_root)

    cfg = ObserverConfig.from_yaml_file(c_path)
    if max_cycles_override is not None and max_cycles_override > 0:
        cfg_dict = cfg.model_dump()
        cfg_dict["maximum_cycles"] = max_cycles_override
        cfg = ObserverConfig(**cfg_dict)

    if not ("127.0.0.1" in cfg.prometheus_url or "localhost" in cfg.prometheus_url):
        print(
            f"ERROR: Prometheus URL must target localhost in guarded dry run, got '{cfg.prometheus_url}'",
            file=sys.stderr,
        )
        return 1

    if cfg.maximum_cycles <= 0 or cfg.maximum_cycles > 50:
        print(
            f"ERROR: Maximum cycles must be in range [1, 50], got {cfg.maximum_cycles}",
            file=sys.stderr,
        )
        return 1

    cap_cfg = CapacityConfig.from_yaml_file(cap_path)
    tg_cfg = load_trust_guard_config(tg_path)
    forecasting_service = ForecastingService.from_config_file(f_path)

    actual_run_id = run_id or f"guarded-dry-run-{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    run_dir = out_root / actual_run_id
    if run_dir.exists():
        print(f"ERROR: Run directory {run_dir} already exists. Aborting.", file=sys.stderr)
        return 1
    run_dir.mkdir(parents=True, exist_ok=False)

    setup_logger(run_dir)
    logger.info("======================================================================")
    logger.info("PHASE 10A GUARDED DRY-RUN CONTROLLER (FORECAST TRUST GUARD)")
    logger.info("======================================================================")
    logger.info("Run ID:             %s", actual_run_id)
    logger.info("Output Dir:         %s", run_dir)
    logger.info("Prometheus URL:     %s", cfg.prometheus_url)
    logger.info("Cycle Interval:     %.1fs", cfg.forecast_interval_seconds)
    logger.info("Max Cycles:         %d", cfg.maximum_cycles)
    logger.info("Frozen Model:       %s", forecasting_service.model_name)
    logger.info("Trust Guard Status: %s", tg_cfg.status)
    logger.info("Absolute Threshold: %.4f req/s", tg_cfg.absolute_error_threshold)
    logger.info("Relative Threshold: %.2f%% sMAPE", tg_cfg.relative_error_threshold)
    logger.info("Min Residuals:      %d", tg_cfg.minimum_matched_residuals)
    logger.info("Max Residual Age:   %.1fs", tg_cfg.residual_max_age_seconds)
    logger.info("Mode:               GUARDED DRY-RUN (action_executed=False, zero actuation)")

    metrics = GuardedDryRunMetrics()
    metrics_server = MetricsServer(
        host=cfg.metrics_host, port=cfg.metrics_port, registry=metrics.registry
    )
    try:
        metrics_server.start()
    except Exception as e:  # noqa: BLE001  # noqa: BLE001
        logger.error(
            "Failed to start metrics server on %s:%d: %s",
            cfg.metrics_host,
            cfg.metrics_port,
            e,
        )
        return 1

    telemetry_reader = LiveTelemetryReader(cfg)
    forecast_store = ForecastStore()
    deployment_reader = ReadOnlyDeploymentReader()

    cycle_results: list[GuardedDryRunCycleResult] = []
    run_status = "completed"
    interrupted = False

    def handle_sigint(signum: Any, frame: Any) -> None:
        nonlocal interrupted
        interrupted = True
        logger.warning("\nInterrupt received. Finishing current cycle and cleaning up...")

    orig_sigint = signal.signal(signal.SIGINT, handle_sigint)

    started_at = datetime.now(UTC)
    try:
        for cycle_num in range(1, cfg.maximum_cycles + 1):
            if interrupted:
                run_status = "interrupted"
                break

            now_utc = datetime.now(UTC)
            res = execute_guarded_dry_run_cycle(
                cycle_number=cycle_num,
                current_time=now_utc,
                observer_config=cfg,
                capacity_config=cap_cfg,
                trust_guard_config=tg_cfg,
                telemetry_reader=telemetry_reader,
                forecasting_service=forecasting_service,
                forecast_store=forecast_store,
                deployment_reader=deployment_reader,
                metrics=metrics,
            )
            cycle_results.append(res)

            if cycle_num < cfg.maximum_cycles and not interrupted:
                time.sleep(cfg.forecast_interval_seconds)

    except Exception:
        logger.exception("Guarded dry-run cycle execution failed")
        run_status = "failed"
    finally:
        completed_at = datetime.now(UTC)
        signal.signal(signal.SIGINT, orig_sigint)
        metrics_server.stop()

        write_guarded_dry_run_artifacts(
            run_dir=run_dir,
            run_id=actual_run_id,
            observer_config=cfg,
            capacity_config=cap_cfg,
            trust_guard_config=tg_cfg,
            forecasting_service=forecasting_service,
            forecast_store=forecast_store,
            cycle_results=cycle_results,
            started_at=started_at,
            completed_at=completed_at,
            status=run_status,
            config_path=c_path,
            capacity_config_path=cap_path,
            trust_guard_config_path=tg_path,
            frozen_forecaster_path=f_path,
        )

    logger.info("Guarded dry-run %s finished with status '%s'.", actual_run_id, run_status)
    return 0 if run_status in ("completed", "interrupted") else 1


def validate_arbitration_mode(
    config_path: str = "config/observer.yaml",
    frozen_forecaster_path: str = "forecasting/config/final_forecaster.yaml",
    capacity_config_path: str = "controller/config/capacity.yaml",
    trust_guard_config_path: str = "controller/config/trust_guard.yaml",
    policy_config_path: str = "controller/config/policy.yaml",
) -> int:
    """Validate all configurations and run deterministic arbitration tests."""
    print("======================================================================")
    print("PHASE 10B DRY-RUN ARBITRATION - CONFIGURATION & DETERMINISTIC VALIDATION")
    print("======================================================================")

    c_path = Path(config_path)
    f_path = Path(frozen_forecaster_path)
    cap_path = Path(capacity_config_path)
    tg_path = Path(trust_guard_config_path)
    pol_path = Path(policy_config_path)

    errors: list[str] = []

    # 1. Observer Config
    try:
        obs_cfg = ObserverConfig.from_yaml_file(c_path)
        print(
            f"[OK] Observer config valid: mode={obs_cfg.mode}, sampling={obs_cfg.sampling_interval_seconds}s"
        )
    except Exception as e:  # noqa: BLE001  # noqa: BLE001
        errors.append(f"ObserverConfig: {e}")
        print(f"[FAIL] ObserverConfig: {e}", file=sys.stderr)

    # 2. Frozen Forecaster
    try:
        svc = ForecastingService.from_config_file(f_path)
        print(f"[OK] Frozen forecaster valid: model={svc.model_name}, status={svc.status}")
    except Exception as e:  # noqa: BLE001  # noqa: BLE001
        errors.append(f"ForecastingService: {e}")
        print(f"[FAIL] ForecastingService: {e}", file=sys.stderr)

    # 3. Capacity Config
    try:
        cap_cfg = CapacityConfig.from_yaml_file(cap_path)
        print(
            f"[OK] Capacity configuration valid: status={cap_cfg.status}, eff_cap={cap_cfg.effective_capacity_per_replica:.2f} req/s"
        )
    except Exception as e:  # noqa: BLE001  # noqa: BLE001
        errors.append(f"CapacityConfig: {e}")
        print(f"[FAIL] CapacityConfig: {e}", file=sys.stderr)

    # 4. Trust Guard Config
    try:
        tg_cfg = load_trust_guard_config(tg_path)
        print(
            f"[OK] Trust Guard configuration valid: status={tg_cfg.status}, MAE threshold={tg_cfg.absolute_error_threshold}"
        )
    except Exception as e:  # noqa: BLE001  # noqa: BLE001
        errors.append(f"TrustGuardConfig: {e}")
        print(f"[FAIL] TrustGuardConfig: {e}", file=sys.stderr)

    # 5. Policy Config
    pol_cfg: PolicyConfig | None = None
    try:
        pol_cfg = load_policy_config(pol_path)
        print(f"[OK] Policy configuration valid: status={pol_cfg.status}")
        print(f"     Rapid Change Window:   {pol_cfg.request_rate_window_samples} samples")
        print(f"     Growth Ratio Thresh:   {pol_cfg.rapid_change_growth_ratio:.1f}x")
        print(f"     Absolute Rate Thresh:  {pol_cfg.rapid_change_absolute_rate:.1f} req/s")
        print(f"     Reactive CPU Target:   {pol_cfg.reactive_cpu_target_ratio * 100:.0f}%")
        print(
            f"     Replica Bounds:        [{pol_cfg.minimum_replicas}, {pol_cfg.maximum_replicas}]"
        )
        print(
            f"     Step Limits:           [+{pol_cfg.maximum_scale_up_step}, -{pol_cfg.maximum_scale_down_step}]"
        )
        print(f"     Cooldown:              {pol_cfg.cooldown_seconds:.1f}s")
        print(f"     Downscale Stabilize:   {pol_cfg.downscale_stabilization_seconds:.1f}s")
    except Exception as e:  # noqa: BLE001  # noqa: BLE001
        errors.append(f"PolicyConfig: {e}")
        print(f"[FAIL] PolicyConfig: {e}", file=sys.stderr)

    if errors or pol_cfg is None:
        print(f"\n[FAIL] Validation encountered {len(errors)} error(s).", file=sys.stderr)
        return 1

    print("\n--- DETERMINISTIC ARBITRATION EXAMPLES ---")
    now_utc = datetime.now(UTC)

    # Example 1: Predictive Mode (Trusted forecast, normal traffic, no rapid change)
    tel_pred = TelemetryResult(
        query_started_at=now_utc - timedelta(seconds=1),
        query_completed_at=now_utc,
        timestamps=[now_utc - timedelta(seconds=30), now_utc - timedelta(seconds=15), now_utc],
        values=[10.0, 10.0, 10.0],
        observation_count=3,
        latest_age_seconds=1.0,
        is_fresh=True,
        is_sufficient=True,
        status="available_fresh",
        expected_interval_seconds=15.0,
    )
    trust_pred = ForecastTrustDecision(
        evaluated_at=now_utc,
        trusted=True,
        outcome=TrustOutcome.TRUSTED,
        forecast_id="fc-1",
        model_name="persistence",
        forecast_generated_at=now_utc - timedelta(seconds=15),
        forecast_target_timestamp=now_utc,
        telemetry_latest_timestamp=now_utc,
        telemetry_age_seconds=1.0,
        telemetry_fresh=True,
        history_observations=5,
        minimum_history_required=1,
        matched_residual_count=5,
        minimum_matched_residuals=3,
        residual_window_start=now_utc - timedelta(seconds=90),
        residual_window_end=now_utc,
        recent_mae=0.5,
        recent_rmse=0.6,
        recent_smape=4.0,
        recent_bias=0.1,
        absolute_error_threshold=tg_cfg.absolute_error_threshold,
        relative_error_threshold=tg_cfg.relative_error_threshold,
        evidence_window_seconds=180.0,
    )
    rc_pred = detect_rapid_change(tel_pred, pol_cfg, now_utc)
    cap_pred = calculate_capacity_recommendation(10.0, 2, cap_cfg)
    cpu_pred = CpuTelemetryResult(
        timestamp=now_utc,
        is_available=True,
        is_fresh=True,
        pod_count=2,
        total_cpu_cores=0.04,
        requested_cpu_cores=0.20,
        cpu_utilization_ratio=0.20,
    )

    dec_pred = arbitrate_policy_decision(
        timestamp=now_utc,
        cycle_number=1,
        current_replicas=2,
        telemetry=tel_pred,
        forecast=None,
        trust_decision=trust_pred,
        rapid_change_result=rc_pred,
        predictive_capacity_result=cap_pred,
        cpu_telemetry=cpu_pred,
        config=pol_cfg,
    )
    assert dec_pred.policy_mode == PolicyMode.PREDICTIVE
    print(
        f"[PASS] Predictive path: mode={dec_pred.policy_mode.value}, reason={dec_pred.primary_reason.value}, rec={dec_pred.final_recommendation}"
    )

    # Example 2: Rapid-Change Override
    tel_surge = TelemetryResult(
        query_started_at=now_utc - timedelta(seconds=1),
        query_completed_at=now_utc,
        timestamps=[now_utc - timedelta(seconds=30), now_utc - timedelta(seconds=15), now_utc],
        values=[2.0, 2.0, 15.0],  # 7.5x increase, delta 13.0
        observation_count=3,
        latest_age_seconds=1.0,
        is_fresh=True,
        is_sufficient=True,
        status="available_fresh",
        expected_interval_seconds=15.0,
    )
    rc_surge = detect_rapid_change(tel_surge, pol_cfg, now_utc)
    assert rc_surge.detected is True
    cpu_surge = CpuTelemetryResult(
        timestamp=now_utc,
        is_available=True,
        is_fresh=True,
        pod_count=2,
        total_cpu_cores=0.18,
        requested_cpu_cores=0.20,
        cpu_utilization_ratio=0.90,  # 90% CPU -> proportional recommendation ceil(2 * 0.90 / 0.70) = 3
    )
    dec_surge = arbitrate_policy_decision(
        timestamp=now_utc,
        cycle_number=2,
        current_replicas=2,
        telemetry=tel_surge,
        forecast=None,
        trust_decision=trust_pred,
        rapid_change_result=rc_surge,
        predictive_capacity_result=cap_pred,
        cpu_telemetry=cpu_surge,
        config=pol_cfg,
    )
    assert dec_surge.policy_mode == PolicyMode.RAPID_CHANGE_OVERRIDE
    print(
        f"[PASS] Rapid-change override path: mode={dec_surge.policy_mode.value}, reason={dec_surge.primary_reason.value}, rec={dec_surge.final_recommendation}"
    )

    # Example 3: Reactive Fallback (Untrusted forecast)
    trust_untrusted = ForecastTrustDecision(
        evaluated_at=now_utc,
        trusted=False,
        outcome=TrustOutcome.INSUFFICIENT_RESIDUAL_HISTORY,
        forecast_id="fc-2",
        model_name="persistence",
        telemetry_fresh=True,
        history_observations=5,
        minimum_history_required=1,
        matched_residual_count=0,
        minimum_matched_residuals=3,
        absolute_error_threshold=tg_cfg.absolute_error_threshold,
        relative_error_threshold=tg_cfg.relative_error_threshold,
        evidence_window_seconds=180.0,
    )
    dec_fallback = arbitrate_policy_decision(
        timestamp=now_utc,
        cycle_number=3,
        current_replicas=2,
        telemetry=tel_pred,
        forecast=None,
        trust_decision=trust_untrusted,
        rapid_change_result=rc_pred,
        predictive_capacity_result=cap_pred,
        cpu_telemetry=cpu_surge,
        config=pol_cfg,
    )
    assert dec_fallback.policy_mode == PolicyMode.REACTIVE_FALLBACK
    print(
        f"[PASS] Reactive fallback path: mode={dec_fallback.policy_mode.value}, reason={dec_fallback.primary_reason.value}, rec={dec_fallback.final_recommendation}"
    )

    # Example 4: No Recommendation Path (Untrusted forecast, CPU unavailable)
    dec_none = arbitrate_policy_decision(
        timestamp=now_utc,
        cycle_number=4,
        current_replicas=2,
        telemetry=tel_pred,
        forecast=None,
        trust_decision=trust_untrusted,
        rapid_change_result=rc_pred,
        predictive_capacity_result=cap_pred,
        cpu_telemetry=None,
        config=pol_cfg,
    )
    assert dec_none.policy_mode == PolicyMode.NO_RECOMMENDATION
    assert dec_none.final_recommendation is None
    print(
        f"[PASS] No-recommendation path: mode={dec_none.policy_mode.value}, reason={dec_none.primary_reason.value}, rec={dec_none.final_recommendation}"
    )

    # Example 5: Cooldown Dampening
    cooldown_mgr = CooldownManager(cooldown_seconds=60.0)
    cooldown_mgr.record_decision(now_utc, final_replicas=4, current_replicas=2)
    dec_cool = arbitrate_policy_decision(
        timestamp=now_utc + timedelta(seconds=15),  # 15s later < 60s
        cycle_number=5,
        current_replicas=2,
        telemetry=tel_pred,
        forecast=None,
        trust_decision=trust_pred,
        rapid_change_result=rc_pred,
        predictive_capacity_result=calculate_capacity_recommendation(40.0, 2, cap_cfg),
        cpu_telemetry=cpu_pred,
        config=pol_cfg,
        cooldown_manager=cooldown_mgr,
    )
    assert dec_cool.cooldown_blocked is True
    assert dec_cool.final_recommendation == 2
    assert dec_cool.primary_reason == DecisionReason.COOLDOWN_ACTIVE
    print(
        f"[PASS] Cooldown dampening: blocked={dec_cool.cooldown_blocked}, reason={dec_cool.primary_reason.value}, effective_rec={dec_cool.final_recommendation}"
    )

    # Example 6: Downscale Stabilization
    stabilizer = DownscaleStabilizer(stabilization_window_seconds=90.0)
    stabilizer.record_recommendation(now_utc - timedelta(seconds=30), 4)
    dec_stab = arbitrate_policy_decision(
        timestamp=now_utc,
        cycle_number=6,
        current_replicas=4,
        telemetry=tel_pred,
        forecast=None,
        trust_decision=trust_pred,
        rapid_change_result=rc_pred,
        predictive_capacity_result=calculate_capacity_recommendation(
            1.0, 4, cap_cfg
        ),  # Wants 1 replica
        cpu_telemetry=cpu_pred,
        config=pol_cfg,
        stabilizer=stabilizer,
    )
    assert "downscale_stabilized" in dec_stab.applied_constraints
    assert dec_stab.final_recommendation == 4
    print(
        f"[PASS] Downscale stabilization: stabilized_rec={dec_stab.stabilized_recommendation}, constraints={dec_stab.applied_constraints}"
    )

    # Static Write Audit
    audit_report = run_write_capability_audit()
    if not audit_report.passed:
        print(
            f"[FAIL] Static write audit failed with {len(audit_report.violations)} violation(s)",
            file=sys.stderr,
        )
        return 1
    print(
        f"[OK] Static write audit passed ({len(audit_report.files_scanned)} files scanned, 0 write calls)"
    )

    print(
        "\n[SUCCESS] Phase 10B Arbitration configuration and deterministic policy logic validated successfully."
    )
    return 0


def arbitration_dry_run_mode(
    config_path: str = "config/observer.yaml",
    capacity_config_path: str = "controller/config/capacity.yaml",
    trust_guard_config_path: str = "controller/config/trust_guard.yaml",
    policy_config_path: str = "controller/config/policy.yaml",
    frozen_forecaster_path: str = "forecasting/config/final_forecaster.yaml",
    output_root: str = "experiments/output/controller-arbitration",
    run_id: str | None = None,
    max_cycles_override: int | None = None,
) -> int:
    """Run bounded arbitrated dry-run controller loop with complete policy arbitration."""
    c_path = Path(config_path)
    cap_path = Path(capacity_config_path)
    tg_path = Path(trust_guard_config_path)
    pol_path = Path(policy_config_path)
    f_path = Path(frozen_forecaster_path)
    out_dir = Path(output_root)

    cfg = ObserverConfig.from_yaml_file(c_path)
    if max_cycles_override is not None:
        cfg = cfg.model_copy(update={"maximum_cycles": max_cycles_override})

    cap_cfg = CapacityConfig.from_yaml_file(cap_path)
    tg_cfg = load_trust_guard_config(tg_path)
    pol_cfg = load_policy_config(pol_path)
    forecasting_service = ForecastingService.from_config_file(f_path)

    actual_run_id = run_id or f"arbitration-dry-run-{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    run_dir = out_dir / actual_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    setup_logger(run_dir)

    logger.info("======================================================================")
    logger.info("PHASE 10B ARBITRATED DRY-RUN CONTROLLER (FORECAST, RAPID CHANGE & CPU)")
    logger.info("======================================================================")
    logger.info("Run ID:             %s", actual_run_id)
    logger.info("Output Dir:         %s", run_dir)
    logger.info("Prometheus URL:     %s", cfg.prometheus_url)
    logger.info("Cycle Interval:     %.1fs", cfg.forecast_interval_seconds)
    logger.info("Max Cycles:         %d", cfg.maximum_cycles)
    logger.info("Frozen Model:       %s", forecasting_service.model_name)
    logger.info("Trust Guard Status: %s", tg_cfg.status)
    logger.info("Policy Status:      %s", pol_cfg.status)
    logger.info("Cooldown:           %.1fs", pol_cfg.cooldown_seconds)
    logger.info("Downscale Window:   %.1fs", pol_cfg.downscale_stabilization_seconds)
    logger.info("Mode:               ARBITRATED DRY-RUN (action_executed=False, zero actuation)")

    metrics = ArbitrationMetrics()
    metrics_server = MetricsServer(
        host=cfg.metrics_host, port=cfg.metrics_port, registry=metrics.registry
    )
    try:
        metrics_server.start()
    except Exception as e:  # noqa: BLE001  # noqa: BLE001
        logger.error(
            "Failed to start metrics server on %s:%d: %s",
            cfg.metrics_host,
            cfg.metrics_port,
            e,
        )
        return 1

    telemetry_reader = LiveTelemetryReader(cfg)
    forecast_store = ForecastStore()
    deployment_reader = ReadOnlyDeploymentReader()
    cpu_telemetry_provider = ReadOnlyCpuTelemetryReader()
    cooldown_mgr = CooldownManager(pol_cfg.cooldown_seconds)
    stabilizer = DownscaleStabilizer(pol_cfg.downscale_stabilization_seconds)

    cycle_results: list[ArbitrationCycleResult] = []
    run_status = "completed"
    interrupted = False

    def handle_sigint(signum: Any, frame: Any) -> None:
        nonlocal interrupted
        interrupted = True
        logger.warning("\nInterrupt received. Finishing current cycle and cleaning up...")

    orig_sigint = signal.signal(signal.SIGINT, handle_sigint)

    started_at = datetime.now(UTC)
    try:
        for cycle_num in range(1, cfg.maximum_cycles + 1):
            if interrupted:
                run_status = "interrupted"
                break

            now_utc = datetime.now(UTC)
            res = execute_arbitration_cycle(
                cycle_number=cycle_num,
                current_time=now_utc,
                observer_config=cfg,
                capacity_config=cap_cfg,
                trust_guard_config=tg_cfg,
                policy_config=pol_cfg,
                telemetry_reader=telemetry_reader,
                forecasting_service=forecasting_service,
                forecast_store=forecast_store,
                deployment_reader=deployment_reader,
                cpu_telemetry_provider=cpu_telemetry_provider,
                metrics=metrics,
                cooldown_manager=cooldown_mgr,
                stabilizer=stabilizer,
            )
            cycle_results.append(res)

            logger.info(
                "Cycle %d: mode=%s, reason=%s, final_rec=%s, direction=%s",
                cycle_num,
                res.policy_mode,
                res.primary_reason,
                res.final_recommendation,
                res.direction,
            )

            if cycle_num < cfg.maximum_cycles and not interrupted:
                time.sleep(cfg.forecast_interval_seconds)

    except Exception:
        logger.exception("Arbitrated dry-run cycle execution failed")
        run_status = "failed"
    finally:
        completed_at = datetime.now(UTC)
        signal.signal(signal.SIGINT, orig_sigint)
        metrics_server.stop()

        persist_arbitration_artifacts(
            run_dir=run_dir,
            run_id=actual_run_id,
            started_at=started_at,
            completed_at=completed_at,
            status=run_status,
            cycle_results=cycle_results,
            forecast_store=forecast_store,
            observer_config=cfg,
            capacity_config=cap_cfg,
            trust_guard_config=tg_cfg,
            policy_config=pol_cfg,
            forecasting_service=forecasting_service,
            policy_config_path=pol_path,
        )

    logger.info("Arbitrated dry-run %s finished with status '%s'.", actual_run_id, run_status)
    return 0 if run_status in ("completed", "interrupted") else 1


def run_controller_mode(
    config_path: str = "config/observer.yaml",
    capacity_config_path: str = "controller/config/capacity.yaml",
    trust_guard_config_path: str = "controller/config/trust_guard.yaml",
    policy_config_path: str = "controller/config/policy.yaml",
    frozen_forecaster_path: str = "forecasting/config/final_forecaster.yaml",
    output_dir: str | None = None,
    max_cycles: int | None = None,
    prometheus_url: str | None = None,
    metrics_host: str = "0.0.0.0",
    metrics_port: int = 9101,
) -> int:
    """Run resilient controller loop (Phase 11 live or dry-run)."""
    c_path = Path(config_path)
    cap_path = Path(capacity_config_path)
    tg_path = Path(trust_guard_config_path)
    pol_path = Path(policy_config_path)
    f_path = Path(frozen_forecaster_path)
    out_path = Path(output_dir) if output_dir else None

    setup_logger(out_path)

    # 1. Load configurations
    try:
        obs_cfg = load_observer_config(c_path)
        cap_cfg = load_capacity_config(cap_path)
        tg_cfg = load_trust_guard_config(tg_path)
        pol_cfg = load_policy_config(pol_path)
        forecaster_service = ForecastingService.from_config_file(f_path)
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to load mandatory controller configuration: %s", e)
        return 1

    # 2. Determine switches & URLs
    controller_mode = os.getenv("CONTROLLER_MODE", "dry_run")
    enable_live = os.getenv("ENABLE_LIVE_SCALING", "false").lower() in ("true", "1", "yes")
    prom_url = prometheus_url or os.getenv("PROMETHEUS_URL") or obs_cfg.prometheus_url

    # 3. Initialize components
    from controller.controller_loop import ControllerLoop
    from controller.kubernetes_scale import KubernetesScaleClient

    registry = CollectorRegistry(auto_describe=True)
    metrics_server = MetricsServer(host=metrics_host, port=metrics_port, registry=registry)
    metrics_server.start()

    if prom_url and prom_url != obs_cfg.prometheus_url:
        obs_cfg = obs_cfg.model_copy(update={"prometheus_url": prom_url})
    telemetry_reader = LiveTelemetryReader(obs_cfg)
    cpu_reader = ReadOnlyCpuTelemetryReader()

    scale_client = KubernetesScaleClient(
        controller_mode=controller_mode,
        enable_live_scaling=enable_live,
        min_replicas=pol_cfg.minimum_replicas,
        max_replicas=pol_cfg.maximum_replicas,
    )

    loop = ControllerLoop(
        observer_config=obs_cfg,
        capacity_config=cap_cfg,
        trust_guard_config=tg_cfg,
        policy_config=pol_cfg,
        forecasting_service=forecaster_service,
        telemetry_reader=telemetry_reader,
        cpu_telemetry_provider=cpu_reader,
        scale_client=scale_client,
        output_dir=out_path,
        max_cycles=max_cycles,
        metrics_server=metrics_server,
        registry=registry,
    )

    try:
        loop.run()
    finally:
        metrics_server.stop()

    return 0


def main() -> int:
    """Main CLI entry point for controller operations."""
    parser = argparse.ArgumentParser(
        prog="python -m controller.cli",
        description="KubePredict Controller CLI (Phase 9A Observe / Phase 9B Dry-Run / Phase 10A Guard / Phase 10B Arbitration)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate subcommand
    p_val = subparsers.add_parser(
        "validate", help="Validate controller configuration, capacity model, and frozen forecaster"
    )
    p_val.add_argument(
        "--config", default="config/observer.yaml", help="Path to controller config YAML"
    )
    p_val.add_argument(
        "--capacity-config",
        default="controller/config/capacity.yaml",
        help="Path to capacity config YAML",
    )
    p_val.add_argument(
        "--frozen-forecaster",
        default="forecasting/config/final_forecaster.yaml",
        help="Path to frozen forecaster YAML",
    )

    # observe subcommand
    p_obs = subparsers.add_parser("observe", help="Run bounded observe-only controller loop")
    p_obs.add_argument(
        "--config", default="config/observer.yaml", help="Path to controller config YAML"
    )
    p_obs.add_argument(
        "--frozen-forecaster",
        default="forecasting/config/final_forecaster.yaml",
        help="Path to frozen forecaster YAML",
    )
    p_obs.add_argument(
        "--output-root",
        default="experiments/output/controller-observe",
        help="Output root directory",
    )
    p_obs.add_argument("--run-id", default=None, help="Explicit run identifier")
    p_obs.add_argument("--max-cycles", type=int, default=None, help="Override maximum cycle count")

    # dry-run subcommand (Phase 9B)
    p_dry = subparsers.add_parser(
        "dry-run", help="Run bounded dry-run controller loop with capacity recommendations"
    )
    p_dry.add_argument(
        "--config", default="config/observer.yaml", help="Path to controller config YAML"
    )
    p_dry.add_argument(
        "--capacity-config",
        default="controller/config/capacity.yaml",
        help="Path to capacity config YAML",
    )
    p_dry.add_argument(
        "--frozen-forecaster",
        default="forecasting/config/final_forecaster.yaml",
        help="Path to frozen forecaster YAML",
    )
    p_dry.add_argument(
        "--output-root",
        default="experiments/output/controller-dry-run",
        help="Output root directory for dry-run artifacts",
    )
    p_dry.add_argument("--run-id", default=None, help="Explicit run identifier")
    p_dry.add_argument("--max-cycles", type=int, default=None, help="Override maximum cycle count")

    # validate-trust-guard subcommand (Phase 10A)
    p_val_tg = subparsers.add_parser(
        "validate-trust-guard",
        help="Validate trust guard configuration, capacity model, frozen forecaster, and run deterministic tests",
    )
    p_val_tg.add_argument(
        "--config", default="config/observer.yaml", help="Path to controller config YAML"
    )
    p_val_tg.add_argument(
        "--capacity-config",
        default="controller/config/capacity.yaml",
        help="Path to capacity config YAML",
    )
    p_val_tg.add_argument(
        "--trust-guard-config",
        default="controller/config/trust_guard.yaml",
        help="Path to trust guard config YAML",
    )
    p_val_tg.add_argument(
        "--frozen-forecaster",
        default="forecasting/config/final_forecaster.yaml",
        help="Path to frozen forecaster YAML",
    )

    # guarded-dry-run subcommand (Phase 10A)
    p_guarded = subparsers.add_parser(
        "guarded-dry-run",
        help="Run bounded guarded dry-run controller loop with Forecast Trust Guard",
    )
    p_guarded.add_argument(
        "--config", default="config/observer.yaml", help="Path to controller config YAML"
    )
    p_guarded.add_argument(
        "--capacity-config",
        default="controller/config/capacity.yaml",
        help="Path to capacity config YAML",
    )
    p_guarded.add_argument(
        "--trust-guard-config",
        default="controller/config/trust_guard.yaml",
        help="Path to trust guard config YAML",
    )
    p_guarded.add_argument(
        "--frozen-forecaster",
        default="forecasting/config/final_forecaster.yaml",
        help="Path to frozen forecaster YAML",
    )
    p_guarded.add_argument(
        "--output-root",
        default="experiments/output/controller-trust-guard",
        help="Output root directory for guarded dry-run artifacts",
    )
    p_guarded.add_argument("--run-id", default=None, help="Explicit run identifier")
    p_guarded.add_argument(
        "--max-cycles", type=int, default=None, help="Override maximum cycle count"
    )

    # validate-arbitration subcommand (Phase 10B)
    p_val_arb = subparsers.add_parser(
        "validate-arbitration",
        help="Validate observer, capacity, trust guard, policy configurations, and deterministic arbitration tests",
    )
    p_val_arb.add_argument(
        "--config", default="config/observer.yaml", help="Path to controller config YAML"
    )
    p_val_arb.add_argument(
        "--capacity-config",
        default="controller/config/capacity.yaml",
        help="Path to capacity config YAML",
    )
    p_val_arb.add_argument(
        "--trust-guard-config",
        default="controller/config/trust_guard.yaml",
        help="Path to trust guard config YAML",
    )
    p_val_arb.add_argument(
        "--policy-config",
        default="controller/config/policy.yaml",
        help="Path to policy config YAML",
    )
    p_val_arb.add_argument(
        "--frozen-forecaster",
        default="forecasting/config/final_forecaster.yaml",
        help="Path to frozen forecaster YAML",
    )

    # arbitration-dry-run subcommand (Phase 10B)
    p_arb = subparsers.add_parser(
        "arbitration-dry-run",
        help="Run bounded dry-run controller loop with complete policy arbitration",
    )
    p_arb.add_argument(
        "--config", default="config/observer.yaml", help="Path to controller config YAML"
    )
    p_arb.add_argument(
        "--capacity-config",
        default="controller/config/capacity.yaml",
        help="Path to capacity config YAML",
    )
    p_arb.add_argument(
        "--trust-guard-config",
        default="controller/config/trust_guard.yaml",
        help="Path to trust guard config YAML",
    )
    p_arb.add_argument(
        "--policy-config",
        default="controller/config/policy.yaml",
        help="Path to policy config YAML",
    )
    p_arb.add_argument(
        "--frozen-forecaster",
        default="forecasting/config/final_forecaster.yaml",
        help="Path to frozen forecaster YAML",
    )
    p_arb.add_argument(
        "--output-root",
        default="experiments/output/controller-arbitration",
        help="Output root directory for arbitration artifacts",
    )
    p_arb.add_argument("--run-id", default=None, help="Explicit run identifier")
    p_arb.add_argument("--max-cycles", type=int, default=None, help="Override maximum cycle count")

    # run-controller subcommand (Phase 11)
    p_ctrl = subparsers.add_parser(
        "run-controller", help="Run the KubePredict controller loop (Phase 11 live or dry-run)"
    )
    p_ctrl.add_argument(
        "--config", default="config/observer.yaml", help="Path to controller config YAML"
    )
    p_ctrl.add_argument(
        "--capacity-config",
        default="controller/config/capacity.yaml",
        help="Path to capacity config YAML",
    )
    p_ctrl.add_argument(
        "--trust-guard-config",
        default="controller/config/trust_guard.yaml",
        help="Path to trust guard config YAML",
    )
    p_ctrl.add_argument(
        "--policy-config",
        default="controller/config/policy.yaml",
        help="Path to policy config YAML",
    )
    p_ctrl.add_argument(
        "--frozen-forecaster",
        default="forecasting/config/final_forecaster.yaml",
        help="Path to frozen forecaster YAML",
    )
    p_ctrl.add_argument("--output-dir", default=None, help="Output directory for cycle evidence")
    p_ctrl.add_argument("--max-cycles", type=int, default=None, help="Optional maximum cycle count")
    p_ctrl.add_argument("--prometheus-url", default=None, help="Prometheus base URL override")
    p_ctrl.add_argument("--metrics-host", default="0.0.0.0", help="Metrics server listen host")
    p_ctrl.add_argument("--metrics-port", type=int, default=9101, help="Metrics server listen port")

    # audit-source subcommand
    subparsers.add_parser(
        "audit-source", help="Execute static write-capability audit on controller code"
    )

    args = parser.parse_args()

    if args.command == "validate":
        return validate_mode(args.config, args.frozen_forecaster, args.capacity_config)
    elif args.command == "observe":
        return observe_mode(
            config_path=args.config,
            frozen_forecaster_path=args.frozen_forecaster,
            output_root=args.output_root,
            run_id=args.run_id,
            max_cycles_override=args.max_cycles,
        )
    elif args.command == "dry-run":
        return dry_run_mode(
            config_path=args.config,
            capacity_config_path=args.capacity_config,
            frozen_forecaster_path=args.frozen_forecaster,
            output_root=args.output_root,
            run_id=args.run_id,
            max_cycles_override=args.max_cycles,
        )
    elif args.command == "validate-trust-guard":
        return validate_trust_guard_mode(
            config_path=args.config,
            frozen_forecaster_path=args.frozen_forecaster,
            capacity_config_path=args.capacity_config,
            trust_guard_config_path=args.trust_guard_config,
        )
    elif args.command == "guarded-dry-run":
        return guarded_dry_run_mode(
            config_path=args.config,
            capacity_config_path=args.capacity_config,
            trust_guard_config_path=args.trust_guard_config,
            frozen_forecaster_path=args.frozen_forecaster,
            output_root=args.output_root,
            run_id=args.run_id,
            max_cycles_override=args.max_cycles,
        )
    elif args.command == "validate-arbitration":
        return validate_arbitration_mode(
            config_path=args.config,
            frozen_forecaster_path=args.frozen_forecaster,
            capacity_config_path=args.capacity_config,
            trust_guard_config_path=args.trust_guard_config,
            policy_config_path=args.policy_config,
        )
    elif args.command == "arbitration-dry-run":
        return arbitration_dry_run_mode(
            config_path=args.config,
            capacity_config_path=args.capacity_config,
            trust_guard_config_path=args.trust_guard_config,
            policy_config_path=args.policy_config,
            frozen_forecaster_path=args.frozen_forecaster,
            output_root=args.output_root,
            run_id=args.run_id,
            max_cycles_override=args.max_cycles,
        )
    elif args.command == "run-controller":
        return run_controller_mode(
            config_path=args.config,
            capacity_config_path=args.capacity_config,
            trust_guard_config_path=args.trust_guard_config,
            policy_config_path=args.policy_config,
            frozen_forecaster_path=args.frozen_forecaster,
            output_dir=args.output_dir,
            max_cycles=args.max_cycles,
            prometheus_url=args.prometheus_url,
            metrics_host=args.metrics_host,
            metrics_port=args.metrics_port,
        )
    elif args.command == "audit-source":
        return audit_source_mode()
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
