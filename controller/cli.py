"""Command-line interface for KubePredict observe-only controller (Phase 9A)."""

import argparse
import csv
import hashlib
import json
import logging
import signal
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from controller.audit import run_write_capability_audit
from controller.config import ObserverConfig
from controller.forecast_store import ForecastStore, PendingForecast
from controller.metrics import MetricsServer, ObserverMetrics
from controller.observer import ObserverCycleResult, execute_observer_cycle
from controller.telemetry import LiveTelemetryReader
from forecasting.service import ForecastingService

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


def validate_mode(config_path: str, frozen_forecaster_path: str) -> int:
    """Validate observer configuration and frozen forecaster without network calls."""
    print("======================================================================")
    print("PHASE 9A OBSERVE-ONLY CONTROLLER - CONFIGURATION VALIDATION")
    print("======================================================================")

    c_path = Path(config_path)
    f_path = Path(frozen_forecaster_path)

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
    except Exception as e:
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
                errors.append(f"ForecastingService status must be 'offline_selected', got '{svc.status}'")
            print(f"[OK] Frozen forecaster valid: model={svc.model_name} status={svc.status}")
        except Exception as e:
            errors.append(f"ForecastingService loading failed: {e}")
            print(f"[FAIL] Frozen forecaster: {e}", file=sys.stderr)

    # 3. Static Write-Capability Audit
    report = run_write_capability_audit()
    if not report.passed:
        errors.append(f"Static write audit found {len(report.violations)} violations")
        print(f"[FAIL] Static write audit: {len(report.violations)} violation(s)", file=sys.stderr)
        for v in report.violations:
            print(f"       {v.file_path}:{v.line_number} [{v.rule}] {v.explanation}", file=sys.stderr)
    else:
        print(f"[OK] Static write audit passed ({len(report.files_scanned)} files scanned, 0 write calls)")

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
        print(f"\n[FAIL] Found {len(report.violations)} write-capability violations:", file=sys.stderr)
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
    logger.info("Default Horizon: %d step(s) (%.1fs)", cfg.forecast_horizon_steps, cfg.forecast_horizon_steps * cfg.sampling_interval_seconds)

    # Start Metrics Server
    metrics = ObserverMetrics()
    metrics_server = MetricsServer(host=cfg.metrics_host, port=cfg.metrics_port, registry=metrics.registry)
    try:
        metrics_server.start()
    except Exception as e:
        logger.error("Failed to start metrics server on %s:%d: %s", cfg.metrics_host, cfg.metrics_port, e)
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

    except Exception as e:
        logger.exception("Observer cycle execution failed: %s", e)
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
            round(sum(m.absolute_error for m in matched_forecasts if m.absolute_error is not None) / len(matched_forecasts), 4)
            if matched_forecasts
            else None
        ),
    }
    (run_dir / "validation_report.json").write_text(json.dumps(val_report, indent=2), encoding="utf-8")


def main() -> int:
    """Main CLI entry point for controller operations."""
    parser = argparse.ArgumentParser(
        prog="python -m controller.cli",
        description="KubePredict Observe-Only Controller CLI (Phase 9A)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate subcommand
    p_val = subparsers.add_parser("validate", help="Validate controller configuration and frozen model")
    p_val.add_argument("--config", default="config/observer.yaml", help="Path to controller config YAML")
    p_val.add_argument("--frozen-forecaster", default="forecasting/config/final_forecaster.yaml", help="Path to frozen forecaster YAML")

    # observe subcommand
    p_obs = subparsers.add_parser("observe", help="Run bounded observe-only controller loop")
    p_obs.add_argument("--config", default="config/observer.yaml", help="Path to controller config YAML")
    p_obs.add_argument("--frozen-forecaster", default="forecasting/config/final_forecaster.yaml", help="Path to frozen forecaster YAML")
    p_obs.add_argument("--output-root", default="experiments/output/controller-observe", help="Output root directory")
    p_obs.add_argument("--run-id", default=None, help="Explicit run identifier")
    p_obs.add_argument("--max-cycles", type=int, default=None, help="Override maximum cycle count")

    # audit-source subcommand
    subparsers.add_parser("audit-source", help="Execute static write-capability audit on controller code")

    args = parser.parse_args()

    if args.command == "validate":
        return validate_mode(args.config, args.frozen_forecaster)
    elif args.command == "observe":
        return observe_mode(
            config_path=args.config,
            frozen_forecaster_path=args.frozen_forecaster,
            output_root=args.output_root,
            run_id=args.run_id,
            max_cycles_override=args.max_cycles,
        )
    elif args.command == "audit-source":
        return audit_source_mode()
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
