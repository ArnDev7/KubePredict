"""Resilient long-running Kubernetes autoscaling controller loop (Phase 11).

Integrates the complete Phase 10B arbitration policy pipeline with the least-privilege
Phase 11 Kubernetes scale subresource client:
- Reads Prometheus request-rate telemetry
- Point forecasting with frozen Forecaster (ForecastingService)
- Forecast Trust Guard evaluation
- Rapid-change surge detection
- Read-only pod CPU utilization collection
- Predictive & reactive recommendation generation
- Arbitration, bounds, step limits, cooldown, and downscale stabilization
- Scale subresource patching ONLY when dual live switches are satisfied:
  CONTROLLER_MODE=live and ENABLE_LIVE_SCALING=true
- Single-writer lock and duplicate-decision suppression
- Metrics endpoint with healthz and readyz probes
- Graceful shutdown on SIGTERM / SIGINT
"""

from __future__ import annotations

import csv
import logging
import os
import signal
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from prometheus_client import CollectorRegistry

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
from controller.kubernetes_scale import (
    KubernetesScaleClient,
    ScaleWriteResult,
)
from controller.metrics import (
    ArbitrationMetrics,
    LiveControllerMetrics,
    MetricsServer,
)
from controller.policy_config import PolicyConfig
from controller.rapid_change import RapidChangeResult, detect_rapid_change
from controller.stabilization import DownscaleStabilizer
from controller.telemetry import LiveTelemetryReader, TelemetryResult
from controller.trust_config import TrustGuardConfig
from controller.trust_guard import evaluate_forecast_trust
from forecasting.base import ForecastInput
from forecasting.service import ForecastingService
from shared.enums import TrustOutcome
from shared.models import ForecastResult, ForecastTrustDecision
from shared.validation import require_timezone_aware

logger = logging.getLogger(__name__)


class ControllerLoop:
    """Production-grade resilient Kubernetes controller loop."""

    def __init__(
        self,
        observer_config: ObserverConfig,
        capacity_config: CapacityConfig,
        trust_guard_config: TrustGuardConfig,
        policy_config: PolicyConfig,
        forecasting_service: ForecastingService,
        telemetry_reader: LiveTelemetryReader,
        cpu_telemetry_provider: CpuTelemetryProvider,
        scale_client: KubernetesScaleClient,
        output_dir: Path | None = None,
        max_cycles: int | None = None,
        metrics_server: MetricsServer | None = None,
        registry: CollectorRegistry | None = None,
    ) -> None:
        self.observer_config = observer_config
        self.capacity_config = capacity_config
        self.trust_guard_config = trust_guard_config
        self.policy_config = policy_config
        self.forecasting_service = forecasting_service
        self.telemetry_reader = telemetry_reader
        self.cpu_telemetry_provider = cpu_telemetry_provider
        self.scale_client = scale_client
        self.output_dir = output_dir
        self.max_cycles = max_cycles
        self.metrics_server = metrics_server

        self.registry = registry if registry is not None else CollectorRegistry(auto_describe=True)
        self.arb_metrics = ArbitrationMetrics(self.registry)
        self.live_metrics = LiveControllerMetrics(self.registry)

        self.forecast_store = ForecastStore()
        self.cooldown_mgr = CooldownManager(policy_config.cooldown_seconds)
        self.stabilizer = DownscaleStabilizer(policy_config.downscale_stabilization_seconds)

        self._running = False
        self._stop_requested = False
        self._cycle_number = 0

        # Output artifact storage
        self._decisions: list[ArbitrationDecisionRecord] = []
        self._scale_results: list[ScaleWriteResult] = []

    def request_stop(self, signum: int | None = None, frame: Any = None) -> None:
        """Signal the controller loop to stop gracefully after the current cycle."""
        logger.info(
            "Termination signal received (signum=%s). Stopping controller loop gracefully...",
            signum,
        )
        self._stop_requested = True
        self._running = False

    def run(self) -> None:
        """Execute the controller loop indefinitely or up to max_cycles."""
        self._running = True
        self._stop_requested = False

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)

        interval = self.observer_config.sampling_interval_seconds
        logger.info(
            "Controller loop started (interval=%.1fs, mode=%s, live_scaling=%s)",
            interval,
            os.getenv("CONTROLLER_MODE", "dry_run"),
            os.getenv("ENABLE_LIVE_SCALING", "false"),
        )

        # Update live enabled gauge
        self.live_metrics.controller_live_enabled.set(
            1.0 if self.scale_client.is_live_enabled else 0.0
        )

        if self.metrics_server:
            self.metrics_server.set_ready(True)

        try:
            while self._running and not self._stop_requested:
                cycle_start = time.perf_counter()
                self._cycle_number += 1

                try:
                    self.execute_cycle(self._cycle_number)
                except Exception:
                    logger.exception(
                        "Uncaught exception in controller cycle %d", self._cycle_number
                    )
                    self.live_metrics.controller_cycle_errors_total.labels(
                        category="cycle_execution"
                    ).inc()

                if self.max_cycles and self._cycle_number >= self.max_cycles:
                    logger.info(
                        "Reached maximum configured cycles (%d). Stopping.", self.max_cycles
                    )
                    break

                elapsed = time.perf_counter() - cycle_start
                sleep_sec = max(0.0, interval - elapsed)
                if sleep_sec > 0 and self._running and not self._stop_requested:
                    # Sleep in small increments to respond quickly to shutdown signals
                    deadline = time.time() + sleep_sec
                    while time.time() < deadline and not self._stop_requested:
                        time.sleep(min(0.5, deadline - time.time()))

        finally:
            self._running = False
            logger.info("Controller loop terminated after %d cycles.", self._cycle_number)
            if self.output_dir:
                self.persist_evidence()

    def execute_cycle(
        self, cycle_number: int
    ) -> tuple[ArbitrationDecisionRecord, ScaleWriteResult | None]:
        """Execute a single complete controller cycle."""
        current_time = require_timezone_aware(datetime.now(UTC))

        # 1. Telemetry Query
        try:
            telemetry: TelemetryResult = self.telemetry_reader.read_history(
                current_time=current_time,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Telemetry read failed in cycle %d: %s", cycle_number, e)
            self.live_metrics.controller_cycle_errors_total.labels(category="telemetry_query").inc()
            telemetry = TelemetryResult(
                query_started_at=current_time,
                query_completed_at=current_time,
                expected_interval_seconds=self.observer_config.sampling_interval_seconds,
                timestamps=[],
                values=[],
                observation_count=0,
                missing_interval_count=0,
                latest_age_seconds=float("inf"),
                is_fresh=False,
                is_sufficient=False,
                status="unavailable",
                warnings=[f"Telemetry read failed: {e}"],
            )

        # 2. Match pending forecasts whose target time has arrived
        if telemetry.timestamps and telemetry.values:
            self.forecast_store.match_actuals(
                current_time=current_time,
                observation_timestamps=telemetry.timestamps,
                observation_values=telemetry.values,
                tolerance_seconds=self.observer_config.sampling_interval_seconds,
            )

        # 3. Generate frozen point forecast
        point_forecast: ForecastResult | None = None
        pending_fc: PendingForecast | None = None

        if (
            telemetry.status not in ("unavailable", "malformed", "stale")
            and telemetry.is_sufficient
            and telemetry.is_fresh
        ):
            try:
                fc_input = ForecastInput(
                    generated_at=current_time,
                    sampling_interval_seconds=self.observer_config.sampling_interval_seconds,
                    observations=telemetry.values,
                    observation_timestamps=telemetry.timestamps,
                    requested_horizon_steps=self.observer_config.forecast_horizon_steps,
                )
                forecast_output = self.forecasting_service.predict(fc_input)

                target_ts = current_time + timedelta(seconds=forecast_output.horizon_seconds)
                pending_fc = PendingForecast(
                    generated_at=current_time,
                    target_timestamp=target_ts,
                    horizon_seconds=forecast_output.horizon_seconds,
                    model_name=forecast_output.model_name,
                    predicted_request_rate_per_second=forecast_output.predicted_request_rate_per_second,
                    fallback_used=forecast_output.fallback_used,
                    history_count=forecast_output.history_count,
                    warnings=forecast_output.warnings,
                )
                self.forecast_store.add(pending_fc)

                point_forecast = ForecastResult(
                    generated_at=current_time,
                    horizon_seconds=int(forecast_output.horizon_seconds),
                    predicted_request_rate_per_second=pending_fc.predicted_request_rate_per_second,
                    confidence=1.0,
                    model_name=forecast_output.model_name,
                    observations_used=forecast_output.history_count,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Forecasting failed in cycle %d: %s", cycle_number, e)
                self.live_metrics.controller_cycle_errors_total.labels(
                    category="forecast_prediction"
                ).inc()

        # 4. Evaluate Forecast Trust Guard (Phase 10A)
        trust_decision: ForecastTrustDecision | None = None
        if point_forecast is not None and pending_fc is not None:
            try:
                trust_decision = evaluate_forecast_trust(
                    current_time=current_time,
                    config=self.trust_guard_config,
                    telemetry=telemetry,
                    forecast=pending_fc,
                    candidate_residuals=self.forecast_store.get_matched(),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Trust evaluation failed in cycle %d: %s", cycle_number, e)
                self.live_metrics.controller_cycle_errors_total.labels(category="trust_guard").inc()
                trust_decision = ForecastTrustDecision(
                    evaluated_at=current_time,
                    trusted=False,
                    outcome=TrustOutcome.ERROR_THRESHOLD_EXCEEDED,
                    model_name=point_forecast.model_name,
                    telemetry_fresh=telemetry.is_fresh,
                    history_observations=len(telemetry.values),
                    minimum_history_required=self.trust_guard_config.minimum_history_observations,
                    matched_residual_count=0,
                    minimum_matched_residuals=self.trust_guard_config.minimum_matched_residuals,
                    absolute_error_threshold=self.trust_guard_config.absolute_error_threshold,
                    relative_error_threshold=self.trust_guard_config.relative_error_threshold,
                    evidence_window_seconds=self.trust_guard_config.evidence_window_seconds,
                )

        # 5. Evaluate Rapid-Change Detector (Phase 10B)
        rapid_change_res: RapidChangeResult = detect_rapid_change(
            telemetry=telemetry,
            config=self.policy_config,
            evaluated_at=current_time,
        )

        # 6. Current Cluster Workload Replicas
        current_replicas, read_err = self.scale_client.read_workload_scale()
        if current_replicas is None:
            logger.warning("Could not read workload scale (using 2 as fallback): %s", read_err)
            current_replicas = 2

        # 7. Predictive Capacity Recommendation if trusted
        predictive_cap_res: CapacityResult | None = None
        if (
            trust_decision is not None
            and trust_decision.trusted
            and point_forecast is not None
            and point_forecast.predicted_request_rate_per_second is not None
        ):
            predictive_cap_res = calculate_capacity_recommendation(
                predicted_request_rate_per_second=point_forecast.predicted_request_rate_per_second,
                current_replicas=current_replicas,
                config=self.capacity_config,
            )

        # 8. Fetch read-only CPU telemetry
        cpu_telemetry: CpuTelemetryResult | None = None
        try:
            cpu_telemetry = self.cpu_telemetry_provider.get_cpu_telemetry()
        except Exception as e:  # noqa: BLE001
            logger.warning("CPU telemetry collection failed in cycle %d: %s", cycle_number, e)
            self.live_metrics.controller_cycle_errors_total.labels(category="cpu_telemetry").inc()

        # 9. Pure Policy Arbitration
        decision_record: ArbitrationDecisionRecord = arbitrate_policy_decision(
            timestamp=current_time,
            cycle_number=cycle_number,
            current_replicas=current_replicas,
            telemetry=telemetry,
            forecast=point_forecast,
            trust_decision=trust_decision,
            rapid_change_result=rapid_change_res,
            predictive_capacity_result=predictive_cap_res,
            cpu_telemetry=cpu_telemetry,
            config=self.policy_config,
            cooldown_manager=self.cooldown_mgr,
            stabilizer=self.stabilizer,
        )

        # 10. Update Operational Metrics
        self.arb_metrics.record_arbitration_decision(decision_record)
        self.arb_metrics.cycles_total.labels(outcome="arbitration_cycle_completed").inc()

        # 11. Scale Subresource Actuation (Live or Dry-Run)
        scale_write_res: ScaleWriteResult | None = None
        if self.scale_client.is_live_enabled:
            # Live scaling enabled: call the scale client
            scale_write_res = self.scale_client.scale_workload(decision_record, current_time)
            if scale_write_res.succeeded:
                logger.info(
                    "CYCLE %d LIVE SCALE SUCCESS: %d -> %d replicas",
                    cycle_number,
                    scale_write_res.previous_replicas,
                    scale_write_res.requested_replicas,
                )
                self.cooldown_mgr.record_decision(
                    decision_time=current_time,
                    final_replicas=scale_write_res.requested_replicas,
                    current_replicas=scale_write_res.previous_replicas,
                )
            else:
                logger.info(
                    "CYCLE %d LIVE SCALE REJECTED/SKIPPED: reason=%s",
                    cycle_number,
                    scale_write_res.rejection_reason,
                )
            self.live_metrics.record_scale_result(
                scale_write_res,
                decision_record.policy_mode.value,
                decision_record.direction.value,
            )
        else:
            # Dry-run: record simulated write rejection/skip
            scale_write_res = self.scale_client.scale_workload(decision_record, current_time)
            self.live_metrics.record_scale_result(
                scale_write_res,
                decision_record.policy_mode.value,
                decision_record.direction.value,
            )
            logger.info(
                "CYCLE %d DRY-RUN: mode=%s, reason=%s, final_rec=%s, direction=%s",
                cycle_number,
                decision_record.policy_mode.value,
                decision_record.primary_reason.value,
                decision_record.final_recommendation,
                decision_record.direction.value,
            )

        self._decisions.append(decision_record)
        if scale_write_res:
            self._scale_results.append(scale_write_res)

        return decision_record, scale_write_res

    def persist_evidence(self) -> None:
        """Persist cycle decisions and scaling write evidence to output directory."""
        if not self.output_dir:
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 1. Write cycles.jsonl
        cycles_file = self.output_dir / "cycles.jsonl"
        with cycles_file.open("w", encoding="utf-8") as f:
            for d in self._decisions:
                f.write(d.model_dump_json() + "\n")

        # 2. Write scaling_writes.csv
        writes_file = self.output_dir / "scaling_writes.csv"
        with writes_file.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "attempted_at",
                    "completed_at",
                    "decision_id",
                    "target_namespace",
                    "target_deployment",
                    "previous_replicas",
                    "requested_replicas",
                    "observed_replicas_after",
                    "attempted",
                    "succeeded",
                    "rejected",
                    "rejection_reason",
                    "api_status",
                    "retry_count",
                    "conflict_count",
                    "duration_ms",
                ]
            )
            for w in self._scale_results:
                writer.writerow(
                    [
                        w.attempted_at.isoformat(),
                        w.completed_at.isoformat(),
                        w.decision_id,
                        w.target_namespace,
                        w.target_deployment,
                        w.previous_replicas,
                        w.requested_replicas,
                        w.observed_replicas_after or "",
                        w.attempted,
                        w.succeeded,
                        w.rejected,
                        w.rejection_reason or "",
                        w.api_status or "",
                        w.retry_count,
                        w.conflict_count,
                        f"{w.duration_ms:.2f}",
                    ]
                )

        logger.info("Persisted cycle evidence to %s", self.output_dir)
