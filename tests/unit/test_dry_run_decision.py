"""Unit tests for dry-run ScalingDecision builder (Phase 9B)."""

from datetime import UTC, datetime

import pytest

from controller.capacity import calculate_capacity_recommendation
from controller.capacity_config import CapacityConfig
from controller.decision import build_dry_run_decision
from shared.enums import ControllerMode, DecisionReason, ScalingDirection
from shared.models import ForecastResult


@pytest.fixture
def default_capacity_config() -> CapacityConfig:
    return CapacityConfig(
        schema_version="1.0.0",
        status="provisional_calibrated",
        capacity_requests_per_second_per_replica=25.0,
        unit="requests_per_second",
        target_utilization_ratio=0.70,
        safety_margin_ratio=0.90,
        minimum_replicas=1,
        maximum_replicas=10,
        maximum_scale_up_step=3,
        maximum_scale_down_step=1,
        minimum_prediction_rate=0.0,
        capacity_source="phase_7b_fixed_baseline_telemetry",
        capacity_evidence_reference="data/raw/scenarios/dataset-index.json",
        workload_profile="cpu_iterations_100k_delay_20ms_payload_50b",
    )


def test_build_dry_run_decision_scale_up(default_capacity_config):
    now = datetime.now(UTC)
    cap_res = calculate_capacity_recommendation(45.0, 2, default_capacity_config)
    fc = ForecastResult(
        generated_at=now,
        horizon_seconds=15,
        predicted_request_rate_per_second=45.0,
        confidence=1.0,
        model_name="persistence",
        observations_used=1,
    )

    decision = build_dry_run_decision(now, cap_res, forecast=fc)

    assert decision.mode == ControllerMode.DRY_RUN
    assert decision.live_scaling_enabled is False
    assert decision.confidence is None  # Not fabricated
    assert decision.current_replicas == 2
    assert decision.proposed_replicas == 3
    assert decision.final_replicas == 3
    assert decision.direction == ScalingDirection.SCALE_UP
    assert decision.reason == DecisionReason.FORECAST_DEMAND
    assert decision.details["action_executed"] is False


def test_build_dry_run_decision_scale_down(default_capacity_config):
    now = datetime.now(UTC)
    cap_res = calculate_capacity_recommendation(0.0, 2, default_capacity_config)

    decision = build_dry_run_decision(now, cap_res)

    assert decision.mode == ControllerMode.DRY_RUN
    assert decision.live_scaling_enabled is False
    assert decision.confidence is None
    assert decision.current_replicas == 2
    assert decision.proposed_replicas == 1
    assert decision.final_replicas == 1
    assert decision.direction == ScalingDirection.SCALE_DOWN
    assert decision.reason == DecisionReason.MINIMUM_BOUND
    assert decision.details["action_executed"] is False


def test_build_dry_run_decision_no_change(default_capacity_config):
    now = datetime.now(UTC)
    cap_res = calculate_capacity_recommendation(16.0, 2, default_capacity_config)

    decision = build_dry_run_decision(now, cap_res)

    assert decision.mode == ControllerMode.DRY_RUN
    assert decision.live_scaling_enabled is False
    assert decision.current_replicas == 2
    assert decision.proposed_replicas == 2
    assert decision.final_replicas == 2
    assert decision.direction == ScalingDirection.NO_CHANGE
    assert decision.reason == DecisionReason.NO_CHANGE_REQUIRED


def test_timezone_validation(default_capacity_config):
    # Naive timestamp rejected
    naive_dt = datetime(2026, 9, 11, 12, 0, 0)  # noqa: DTZ001
    cap_res = calculate_capacity_recommendation(20.0, 2, default_capacity_config)
    with pytest.raises(ValueError, match="timezone-aware"):
        build_dry_run_decision(naive_dt, cap_res)
