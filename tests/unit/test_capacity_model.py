"""Unit tests for pure deterministic capacity model (Phase 9B)."""

import pytest

from controller.capacity import calculate_capacity_recommendation
from controller.capacity_config import CapacityConfig
from shared.enums import DecisionReason, ScalingDirection


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


def test_zero_predicted_demand(default_capacity_config):
    # Zero demand: unconstrained=0, clamped to min=1, cur=2 -> final=1 (dir=scale_down, reason=minimum_bound)
    res = calculate_capacity_recommendation(0.0, 2, default_capacity_config)
    assert res.unconstrained_required_replicas == 0
    assert res.proposed_replicas == 1
    assert res.final_replicas == 1
    assert res.direction == ScalingDirection.SCALE_DOWN
    assert res.reason == DecisionReason.MINIMUM_BOUND
    assert "minimum_bound" in res.applied_constraints


def test_zero_demand_at_min_replicas(default_capacity_config):
    # Zero demand with cur=1 -> proposed=1, final=1, direction=no_change, reason=minimum_bound
    res = calculate_capacity_recommendation(0.0, 1, default_capacity_config)
    assert res.unconstrained_required_replicas == 0
    assert res.proposed_replicas == 1
    assert res.final_replicas == 1
    assert res.direction == ScalingDirection.NO_CHANGE
    assert res.reason == DecisionReason.MINIMUM_BOUND
    assert "minimum_bound" in res.applied_constraints


def test_demand_below_one_replica_capacity(default_capacity_config):
    # Demand 10.0 req/s < effective cap 15.75 req/s -> unconstrained = 1
    res = calculate_capacity_recommendation(10.0, 2, default_capacity_config)
    assert res.unconstrained_required_replicas == 1
    assert res.proposed_replicas == 1
    assert res.final_replicas == 1
    assert res.direction == ScalingDirection.SCALE_DOWN
    assert res.reason == DecisionReason.FORECAST_DEMAND


def test_exact_capacity_boundary(default_capacity_config):
    # Exact 15.75 req/s -> 15.75 / 15.75 = 1.0 -> ceil = 1
    res = calculate_capacity_recommendation(15.75, 2, default_capacity_config)
    assert res.unconstrained_required_replicas == 1
    assert res.proposed_replicas == 1
    assert res.final_replicas == 1


def test_ceiling_behavior(default_capacity_config):
    # 15.76 req/s -> 15.76 / 15.75 = 1.00063 -> ceil = 2
    res = calculate_capacity_recommendation(15.76, 2, default_capacity_config)
    assert res.unconstrained_required_replicas == 2
    assert res.proposed_replicas == 2
    assert res.final_replicas == 2
    assert res.direction == ScalingDirection.NO_CHANGE
    assert res.reason == DecisionReason.NO_CHANGE_REQUIRED


def test_moderate_scale_up(default_capacity_config):
    # 45 req/s / 15.75 = 2.857 -> ceil = 3. cur=2 -> final=3 (+1 <= max 3)
    res = calculate_capacity_recommendation(45.0, 2, default_capacity_config)
    assert res.unconstrained_required_replicas == 3
    assert res.proposed_replicas == 3
    assert res.final_replicas == 3
    assert res.direction == ScalingDirection.SCALE_UP
    assert res.reason == DecisionReason.FORECAST_DEMAND


def test_scale_up_step_limited(default_capacity_config):
    # 100 req/s / 15.75 = 6.349 -> ceil = 7. cur=2, proposed=7.
    # Step limit is 3, so final=2+3=5
    res = calculate_capacity_recommendation(100.0, 2, default_capacity_config)
    assert res.unconstrained_required_replicas == 7
    assert res.proposed_replicas == 7
    assert res.final_replicas == 5
    assert res.direction == ScalingDirection.SCALE_UP
    assert res.reason == DecisionReason.SCALE_UP_STEP_LIMITED
    assert "scale_up_step_limited" in res.applied_constraints


def test_maximum_bound_clamping(default_capacity_config):
    # 300 req/s / 15.75 = 19.047 -> ceil = 20.
    # Maximum bound is 10. proposed=10.
    # With cur=2, step limit 3 -> final=5.
    res = calculate_capacity_recommendation(300.0, 2, default_capacity_config)
    assert res.unconstrained_required_replicas == 20
    assert res.proposed_replicas == 10
    assert res.final_replicas == 5
    assert "maximum_bound" in res.applied_constraints
    assert "scale_up_step_limited" in res.applied_constraints
    assert res.reason == DecisionReason.SCALE_UP_STEP_LIMITED

    # With cur=9, proposed=10 -> final=10 (+1 <= 3).
    # Here maximum_bound is dominant constraint
    res9 = calculate_capacity_recommendation(300.0, 9, default_capacity_config)
    assert res9.unconstrained_required_replicas == 20
    assert res9.proposed_replicas == 10
    assert res9.final_replicas == 10
    assert res9.reason == DecisionReason.MAXIMUM_BOUND


def test_scale_down_step_limited(default_capacity_config):
    # cur=5, demand=0 -> proposed=1.
    # Step down limit is 1, so final = 5 - 1 = 4.
    res = calculate_capacity_recommendation(0.0, 5, default_capacity_config)
    assert res.unconstrained_required_replicas == 0
    assert res.proposed_replicas == 1
    assert res.final_replicas == 4
    assert res.direction == ScalingDirection.SCALE_DOWN
    assert res.reason == DecisionReason.SCALE_DOWN_STEP_LIMITED
    assert "scale_down_step_limited" in res.applied_constraints


def test_negative_demand_rejected(default_capacity_config):
    with pytest.raises(ValueError, match="cannot be negative"):
        calculate_capacity_recommendation(-5.0, 2, default_capacity_config)


def test_non_finite_demand_rejected(default_capacity_config):
    with pytest.raises(ValueError, match="must be finite"):
        calculate_capacity_recommendation(float("nan"), 2, default_capacity_config)

    with pytest.raises(ValueError, match="must be finite"):
        calculate_capacity_recommendation(float("inf"), 2, default_capacity_config)


def test_invalid_current_replicas_rejected(default_capacity_config):
    with pytest.raises(ValueError, match="cannot be negative"):
        calculate_capacity_recommendation(20.0, -1, default_capacity_config)


def test_determinism_and_immutability(default_capacity_config):
    rate = 42.5
    cur = 2
    res1 = calculate_capacity_recommendation(rate, cur, default_capacity_config)
    res2 = calculate_capacity_recommendation(rate, cur, default_capacity_config)

    assert res1.unconstrained_required_replicas == res2.unconstrained_required_replicas
    assert res1.proposed_replicas == res2.proposed_replicas
    assert res1.final_replicas == res2.final_replicas
    assert res1.direction == res2.direction
    assert res1.reason == res2.reason
    assert res1.applied_constraints == res2.applied_constraints

    # Verify input config unchanged
    assert default_capacity_config.capacity_requests_per_second_per_replica == 25.0
    assert default_capacity_config.minimum_replicas == 1
