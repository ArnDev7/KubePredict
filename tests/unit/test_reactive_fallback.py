"""Unit tests for reactive fallback scaling calculation (Phase 10B)."""

from datetime import UTC, datetime

import pytest

from controller.cpu_telemetry import CpuTelemetryResult
from controller.policy_config import PolicyConfig
from controller.reactive_fallback import calculate_reactive_fallback
from shared.enums import DecisionReason, ScalingDirection


def _create_config(
    cpu_target: float = 0.70,
    min_reps: int = 1,
    max_reps: int = 10,
    max_up: int = 3,
    max_down: int = 1,
) -> PolicyConfig:
    return PolicyConfig(
        request_rate_window_samples=3,
        rapid_change_growth_ratio=2.0,
        rapid_change_absolute_rate=5.0,
        minimum_valid_samples=2,
        reactive_cpu_target_ratio=cpu_target,
        minimum_replicas=min_reps,
        maximum_replicas=max_reps,
        maximum_scale_up_step=max_up,
        maximum_scale_down_step=max_down,
        cooldown_seconds=60.0,
        downscale_stabilization_seconds=90.0,
        source="test",
        evidence_reference="rel/path.yaml",
    )


def _create_cpu(
    ratio: float | None = 0.70, is_avail: bool = True, is_fresh: bool = True
) -> CpuTelemetryResult:
    return CpuTelemetryResult(
        timestamp=datetime.now(UTC),
        is_available=is_avail,
        is_fresh=is_fresh,
        pod_count=2,
        total_cpu_cores=0.14 if ratio is not None else None,
        requested_cpu_cores=0.20,
        cpu_utilization_ratio=ratio,
    )


class TestReactiveFallback:
    def test_cpu_equal_target(self) -> None:
        cfg = _create_config(cpu_target=0.70)
        cpu = _create_cpu(ratio=0.70)
        res = calculate_reactive_fallback(cpu, current_replicas=2, config=cfg)
        assert res.eligible is True
        assert res.raw_unconstrained_replicas == 2
        assert res.final_replicas == 2
        assert res.direction == ScalingDirection.NO_CHANGE
        assert res.reason == DecisionReason.NO_CHANGE_REQUIRED

    def test_cpu_above_target_scale_up(self) -> None:
        cfg = _create_config(cpu_target=0.70)
        # 2 replicas at 100% CPU: ceil(2 * 1.0 / 0.7) = ceil(2.857) = 3
        cpu = _create_cpu(ratio=1.0)
        res = calculate_reactive_fallback(cpu, current_replicas=2, config=cfg)
        assert res.eligible is True
        assert res.raw_unconstrained_replicas == 3
        assert res.final_replicas == 3
        assert res.direction == ScalingDirection.SCALE_UP
        assert res.reason == DecisionReason.REACTIVE_CPU_UTILIZATION

    def test_cpu_below_target_scale_down(self) -> None:
        cfg = _create_config(cpu_target=0.70)
        # 4 replicas at 20% CPU: ceil(4 * 0.2 / 0.7) = ceil(1.14) = 2
        cpu = _create_cpu(ratio=0.20)
        # Step down is 1: from 4 can only go down to 3
        res = calculate_reactive_fallback(cpu, current_replicas=4, config=cfg)
        assert res.eligible is True
        assert res.raw_unconstrained_replicas == 2
        assert res.bounded_replicas == 2
        assert res.final_replicas == 3  # step down limited from 4 to 3
        assert "scale_down_step_limited" in res.applied_constraints
        assert res.direction == ScalingDirection.SCALE_DOWN
        assert res.reason == DecisionReason.SCALE_DOWN_STEP_LIMITED

    def test_ceiling_behavior(self) -> None:
        cfg = _create_config(cpu_target=0.50)
        # 2 replicas at 51% CPU -> ceil(2 * 0.51 / 0.50) = ceil(2.04) = 3
        cpu = _create_cpu(ratio=0.51)
        res = calculate_reactive_fallback(cpu, current_replicas=2, config=cfg)
        assert res.raw_unconstrained_replicas == 3

    def test_minimum_bound(self) -> None:
        cfg = _create_config(min_reps=2, cpu_target=0.70)
        # 2 replicas at 1% CPU -> ceil(2 * 0.01 / 0.70) = 1 -> bounded to 2
        cpu = _create_cpu(ratio=0.01)
        res = calculate_reactive_fallback(cpu, current_replicas=2, config=cfg)
        assert res.raw_unconstrained_replicas == 1
        assert res.bounded_replicas == 2
        assert "minimum_bound" in res.applied_constraints
        assert res.final_replicas == 2

    def test_maximum_bound(self) -> None:
        cfg = _create_config(max_reps=5, max_up=10, cpu_target=0.10)
        # 2 replicas at 100% CPU on 10% target -> ceil(20) = 20 -> bounded to 5
        cpu = _create_cpu(ratio=1.0)
        res = calculate_reactive_fallback(cpu, current_replicas=2, config=cfg)
        assert res.raw_unconstrained_replicas == 20
        assert res.bounded_replicas == 5
        assert "maximum_bound" in res.applied_constraints
        assert res.final_replicas == 5

    def test_missing_cpu_produces_no_recommendation(self) -> None:
        cfg = _create_config()
        cpu = _create_cpu(is_avail=False)
        res = calculate_reactive_fallback(cpu, current_replicas=2, config=cfg)
        assert res.eligible is False
        assert res.final_replicas is None
        assert res.reason == DecisionReason.TELEMETRY_UNAVAILABLE

    def test_stale_cpu(self) -> None:
        cfg = _create_config()
        cpu = _create_cpu(is_fresh=False)
        res = calculate_reactive_fallback(cpu, current_replicas=2, config=cfg)
        assert res.eligible is False
        assert res.final_replicas is None
        assert res.reason == DecisionReason.TELEMETRY_STALE

    def test_invalid_cpu_ratio(self) -> None:
        cfg = _create_config()
        cpu = _create_cpu(ratio=float("nan"))
        res = calculate_reactive_fallback(cpu, current_replicas=2, config=cfg)
        assert res.eligible is False
        assert res.final_replicas is None

    def test_zero_replicas_rejected(self) -> None:
        cfg = _create_config()
        cpu = _create_cpu()
        with pytest.raises(ValueError, match="current_replicas must be > 0"):
            calculate_reactive_fallback(cpu, current_replicas=0, config=cfg)
