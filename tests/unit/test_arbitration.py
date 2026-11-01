"""Unit tests for arbitration policy engine (Phase 10B)."""

from datetime import UTC, datetime, timedelta

from controller.arbitration import arbitrate_policy_decision
from controller.capacity import CapacityResult
from controller.cooldown import CooldownManager
from controller.cpu_telemetry import CpuTelemetryResult
from controller.policy_config import PolicyConfig
from controller.rapid_change import RapidChangeResult
from controller.stabilization import DownscaleStabilizer
from controller.telemetry import TelemetryResult
from shared.enums import DecisionReason, PolicyMode, ScalingDirection, TrustOutcome
from shared.models import ForecastTrustDecision


def _create_config() -> PolicyConfig:
    return PolicyConfig(
        request_rate_window_samples=3,
        rapid_change_growth_ratio=2.0,
        rapid_change_absolute_rate=5.0,
        minimum_valid_samples=2,
        reactive_cpu_target_ratio=0.70,
        minimum_replicas=1,
        maximum_replicas=10,
        maximum_scale_up_step=3,
        maximum_scale_down_step=1,
        cooldown_seconds=60.0,
        downscale_stabilization_seconds=90.0,
        source="test",
        evidence_reference="rel/path.yaml",
    )


def _create_telemetry(status: str = "available_fresh", is_fresh: bool = True) -> TelemetryResult:
    now = datetime.now(UTC)
    return TelemetryResult(
        query_started_at=now - timedelta(seconds=1),
        query_completed_at=now,
        timestamps=[now - timedelta(seconds=30), now - timedelta(seconds=15), now],
        values=[10.0, 10.0, 10.0],
        observation_count=3,
        latest_age_seconds=1.0 if is_fresh else 120.0,
        is_fresh=is_fresh,
        is_sufficient=True,
        status=status,  # type: ignore[arg-type]
        expected_interval_seconds=15.0,
    )


def _create_trust(trusted: bool = True) -> ForecastTrustDecision:
    now = datetime.now(UTC)
    return ForecastTrustDecision(
        evaluated_at=now,
        trusted=trusted,
        outcome=TrustOutcome.TRUSTED if trusted else TrustOutcome.ERROR_THRESHOLD_EXCEEDED,
        forecast_id="fc-1" if trusted else None,
        model_name="persistence",
        telemetry_fresh=True,
        history_observations=5,
        minimum_history_required=1,
        matched_residual_count=5 if trusted else 0,
        minimum_matched_residuals=3,
        recent_mae=0.5 if trusted else None,
        recent_smape=4.0 if trusted else None,
        absolute_error_threshold=10.0,
        relative_error_threshold=50.0,
        evidence_window_seconds=180.0,
    )


def _create_capacity_result(unconstrained: int = 3) -> CapacityResult:
    return CapacityResult(
        predicted_request_rate_per_second=30.0,
        current_replicas=2,
        effective_capacity_per_replica=15.75,
        unconstrained_required_replicas=unconstrained,
        proposed_replicas=unconstrained,
        final_replicas=unconstrained,
        direction=ScalingDirection.SCALE_UP,
        reason=DecisionReason.FORECAST_DEMAND,
    )


def _create_cpu(ratio: float = 0.50, is_avail: bool = True) -> CpuTelemetryResult:
    return CpuTelemetryResult(
        timestamp=datetime.now(UTC),
        is_available=is_avail,
        is_fresh=True,
        pod_count=2,
        total_cpu_cores=0.10,
        requested_cpu_cores=0.20,
        cpu_utilization_ratio=ratio,
    )


class TestArbitrationEngine:
    def test_trusted_forecast_selects_predictive(self) -> None:
        cfg = _create_config()
        now = datetime.now(UTC)
        dec = arbitrate_policy_decision(
            timestamp=now,
            cycle_number=1,
            current_replicas=2,
            telemetry=_create_telemetry(),
            forecast=None,
            trust_decision=_create_trust(trusted=True),
            rapid_change_result=None,
            predictive_capacity_result=_create_capacity_result(unconstrained=3),
            cpu_telemetry=_create_cpu(),
            config=cfg,
        )
        assert dec.policy_mode == PolicyMode.PREDICTIVE
        assert dec.primary_reason == DecisionReason.TRUSTED_FORECAST
        assert dec.final_recommendation == 3
        assert dec.action_executed is False

    def test_trusted_forecast_plus_rapid_change_selects_override(self) -> None:
        cfg = _create_config()
        now = datetime.now(UTC)
        rc = RapidChangeResult(
            evaluated_at=now,
            detected=True,  # Rapid change detected!
            sample_count=3,
            recent_rate=20.0,
            baseline_rate=2.0,
            absolute_increase=18.0,
            growth_ratio=10.0,
            reason=DecisionReason.RAPID_CHANGE_DETECTED,
        )
        # CPU ratio 85% on target 70%: ceil(2 * 0.85 / 0.70) = 3
        cpu = _create_cpu(ratio=0.85)

        dec = arbitrate_policy_decision(
            timestamp=now,
            cycle_number=1,
            current_replicas=2,
            telemetry=_create_telemetry(),
            forecast=None,
            trust_decision=_create_trust(trusted=True),
            rapid_change_result=rc,
            predictive_capacity_result=_create_capacity_result(unconstrained=2),
            cpu_telemetry=cpu,
            config=cfg,
        )
        assert dec.policy_mode == PolicyMode.RAPID_CHANGE_OVERRIDE
        assert dec.primary_reason == DecisionReason.RAPID_CHANGE_DETECTED
        assert dec.final_recommendation == 3
        assert dec.action_executed is False

    def test_untrusted_forecast_selects_reactive_fallback(self) -> None:
        cfg = _create_config()
        now = datetime.now(UTC)
        cpu = _create_cpu(ratio=0.90)  # ceil(2 * 0.90 / 0.70) = 3

        dec = arbitrate_policy_decision(
            timestamp=now,
            cycle_number=1,
            current_replicas=2,
            telemetry=_create_telemetry(),
            forecast=None,
            trust_decision=_create_trust(trusted=False),
            rapid_change_result=None,
            predictive_capacity_result=None,
            cpu_telemetry=cpu,
            config=cfg,
        )
        assert dec.policy_mode == PolicyMode.REACTIVE_FALLBACK
        assert dec.primary_reason == DecisionReason.REACTIVE_CPU_UTILIZATION
        assert dec.final_recommendation == 3
        assert dec.action_executed is False

    def test_untrusted_forecast_with_missing_cpu_produces_no_recommendation(self) -> None:
        cfg = _create_config()
        now = datetime.now(UTC)
        cpu = _create_cpu(is_avail=False)

        dec = arbitrate_policy_decision(
            timestamp=now,
            cycle_number=1,
            current_replicas=2,
            telemetry=_create_telemetry(),
            forecast=None,
            trust_decision=_create_trust(trusted=False),
            rapid_change_result=None,
            predictive_capacity_result=None,
            cpu_telemetry=cpu,
            config=cfg,
        )
        assert dec.policy_mode == PolicyMode.NO_RECOMMENDATION
        assert dec.primary_reason == DecisionReason.FORECAST_UNTRUSTED
        assert dec.final_recommendation is None
        assert dec.direction == ScalingDirection.NO_CHANGE

    def test_stale_telemetry_produces_no_recommendation(self) -> None:
        cfg = _create_config()
        now = datetime.now(UTC)
        dec = arbitrate_policy_decision(
            timestamp=now,
            cycle_number=1,
            current_replicas=2,
            telemetry=_create_telemetry(status="stale", is_fresh=False),
            forecast=None,
            trust_decision=None,
            rapid_change_result=None,
            predictive_capacity_result=None,
            cpu_telemetry=_create_cpu(),
            config=cfg,
        )
        assert dec.policy_mode == PolicyMode.NO_RECOMMENDATION
        assert dec.primary_reason == DecisionReason.TELEMETRY_STALE
        assert dec.final_recommendation is None

    def test_cooldown_blocks_decision_in_engine(self) -> None:
        cfg = _create_config()
        now = datetime.now(UTC)
        cooldown = CooldownManager(cooldown_seconds=60.0)
        cooldown.record_decision(now, final_replicas=4, current_replicas=2)

        dec = arbitrate_policy_decision(
            timestamp=now + timedelta(seconds=20),
            cycle_number=2,
            current_replicas=2,
            telemetry=_create_telemetry(),
            forecast=None,
            trust_decision=_create_trust(trusted=True),
            rapid_change_result=None,
            predictive_capacity_result=_create_capacity_result(unconstrained=5),
            cpu_telemetry=_create_cpu(),
            config=cfg,
            cooldown_manager=cooldown,
        )
        assert dec.cooldown_blocked is True
        assert dec.final_recommendation == 2
        assert dec.primary_reason == DecisionReason.COOLDOWN_ACTIVE

    def test_downscale_stabilization_in_engine(self) -> None:
        cfg = _create_config()
        now = datetime.now(UTC)
        stabilizer = DownscaleStabilizer(stabilization_window_seconds=90.0)
        stabilizer.record_recommendation(now - timedelta(seconds=30), 4)

        # Wants to scale down to 1
        cap = CapacityResult(
            predicted_request_rate_per_second=1.0,
            current_replicas=4,
            effective_capacity_per_replica=15.75,
            unconstrained_required_replicas=1,
            proposed_replicas=1,
            final_replicas=1,
            direction=ScalingDirection.SCALE_DOWN,
            reason=DecisionReason.FORECAST_DEMAND,
        )
        dec = arbitrate_policy_decision(
            timestamp=now,
            cycle_number=2,
            current_replicas=4,
            telemetry=_create_telemetry(),
            forecast=None,
            trust_decision=_create_trust(trusted=True),
            rapid_change_result=None,
            predictive_capacity_result=cap,
            cpu_telemetry=_create_cpu(),
            config=cfg,
            stabilizer=stabilizer,
        )
        assert "downscale_stabilized" in dec.applied_constraints
        assert dec.final_recommendation == 4
        assert dec.direction == ScalingDirection.NO_CHANGE
