"""Unit tests for capacity configuration model (Phase 9B)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from controller.capacity_config import CapacityConfig


def test_valid_capacity_config():
    cfg = CapacityConfig(
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
    assert cfg.capacity_requests_per_second_per_replica == 25.0
    assert cfg.effective_capacity_per_replica == 15.75
    assert cfg.status == "provisional_calibrated"


def test_capacity_config_from_yaml():
    yaml_path = Path("controller/config/capacity.yaml")
    assert yaml_path.exists()
    cfg = CapacityConfig.from_yaml_file(yaml_path)
    assert cfg.capacity_requests_per_second_per_replica == 25.0
    assert cfg.effective_capacity_per_replica == 15.75
    assert cfg.minimum_replicas == 1
    assert cfg.maximum_replicas == 10
    assert cfg.status == "provisional_calibrated"


def test_zero_or_negative_capacity():
    with pytest.raises(ValidationError):
        CapacityConfig(
            capacity_requests_per_second_per_replica=0.0,
            target_utilization_ratio=0.7,
            safety_margin_ratio=0.9,
            minimum_replicas=1,
            maximum_replicas=10,
            maximum_scale_up_step=3,
            maximum_scale_down_step=1,
            capacity_source="test",
            capacity_evidence_reference="test.json",
            workload_profile="test",
        )

    with pytest.raises(ValidationError):
        CapacityConfig(
            capacity_requests_per_second_per_replica=-10.0,
            target_utilization_ratio=0.7,
            safety_margin_ratio=0.9,
            minimum_replicas=1,
            maximum_replicas=10,
            maximum_scale_up_step=3,
            maximum_scale_down_step=1,
            capacity_source="test",
            capacity_evidence_reference="test.json",
            workload_profile="test",
        )


def test_invalid_ratios():
    # Target utilization <= 0 or > 1
    with pytest.raises(ValidationError):
        CapacityConfig(
            capacity_requests_per_second_per_replica=25.0,
            target_utilization_ratio=0.0,
            safety_margin_ratio=0.9,
            minimum_replicas=1,
            maximum_replicas=10,
            maximum_scale_up_step=3,
            maximum_scale_down_step=1,
            capacity_source="test",
            capacity_evidence_reference="test.json",
            workload_profile="test",
        )

    with pytest.raises(ValidationError):
        CapacityConfig(
            capacity_requests_per_second_per_replica=25.0,
            target_utilization_ratio=1.2,
            safety_margin_ratio=0.9,
            minimum_replicas=1,
            maximum_replicas=10,
            maximum_scale_up_step=3,
            maximum_scale_down_step=1,
            capacity_source="test",
            capacity_evidence_reference="test.json",
            workload_profile="test",
        )

    # Safety margin <= 0 or > 1
    with pytest.raises(ValidationError):
        CapacityConfig(
            capacity_requests_per_second_per_replica=25.0,
            target_utilization_ratio=0.7,
            safety_margin_ratio=0.0,
            minimum_replicas=1,
            maximum_replicas=10,
            maximum_scale_up_step=3,
            maximum_scale_down_step=1,
            capacity_source="test",
            capacity_evidence_reference="test.json",
            workload_profile="test",
        )

    with pytest.raises(ValidationError):
        CapacityConfig(
            capacity_requests_per_second_per_replica=25.0,
            target_utilization_ratio=0.7,
            safety_margin_ratio=1.5,
            minimum_replicas=1,
            maximum_replicas=10,
            maximum_scale_up_step=3,
            maximum_scale_down_step=1,
            capacity_source="test",
            capacity_evidence_reference="test.json",
            workload_profile="test",
        )


def test_invalid_replica_bounds():
    # min > max
    with pytest.raises(ValidationError, match="minimum_replicas .* must be <= maximum_replicas"):
        CapacityConfig(
            capacity_requests_per_second_per_replica=25.0,
            target_utilization_ratio=0.7,
            safety_margin_ratio=0.9,
            minimum_replicas=15,
            maximum_replicas=10,
            maximum_scale_up_step=3,
            maximum_scale_down_step=1,
            capacity_source="test",
            capacity_evidence_reference="test.json",
            workload_profile="test",
        )

    # min <= 0
    with pytest.raises(ValidationError):
        CapacityConfig(
            capacity_requests_per_second_per_replica=25.0,
            target_utilization_ratio=0.7,
            safety_margin_ratio=0.9,
            minimum_replicas=0,
            maximum_replicas=10,
            maximum_scale_up_step=3,
            maximum_scale_down_step=1,
            capacity_source="test",
            capacity_evidence_reference="test.json",
            workload_profile="test",
        )


def test_invalid_step_sizes():
    with pytest.raises(ValidationError):
        CapacityConfig(
            capacity_requests_per_second_per_replica=25.0,
            target_utilization_ratio=0.7,
            safety_margin_ratio=0.9,
            minimum_replicas=1,
            maximum_replicas=10,
            maximum_scale_up_step=0,
            maximum_scale_down_step=1,
            capacity_source="test",
            capacity_evidence_reference="test.json",
            workload_profile="test",
        )


def test_reject_production_ready_status():
    with pytest.raises(ValidationError, match="status must be one of"):
        CapacityConfig(
            status="production_ready",
            capacity_requests_per_second_per_replica=25.0,
            target_utilization_ratio=0.7,
            safety_margin_ratio=0.9,
            minimum_replicas=1,
            maximum_replicas=10,
            maximum_scale_up_step=3,
            maximum_scale_down_step=1,
            capacity_source="test",
            capacity_evidence_reference="test.json",
            workload_profile="test",
        )


def test_reject_empty_source_and_reference():
    with pytest.raises(ValidationError):
        CapacityConfig(
            capacity_requests_per_second_per_replica=25.0,
            target_utilization_ratio=0.7,
            safety_margin_ratio=0.9,
            minimum_replicas=1,
            maximum_replicas=10,
            maximum_scale_up_step=3,
            maximum_scale_down_step=1,
            capacity_source="   ",
            capacity_evidence_reference="test.json",
            workload_profile="test",
        )

    with pytest.raises(ValidationError):
        CapacityConfig(
            capacity_requests_per_second_per_replica=25.0,
            target_utilization_ratio=0.7,
            safety_margin_ratio=0.9,
            minimum_replicas=1,
            maximum_replicas=10,
            maximum_scale_up_step=3,
            maximum_scale_down_step=1,
            capacity_source="test",
            capacity_evidence_reference="",
            workload_profile="test",
        )


def test_reject_absolute_evidence_path():
    with pytest.raises(ValidationError, match="must be a relative repository path"):
        CapacityConfig(
            capacity_requests_per_second_per_replica=25.0,
            target_utilization_ratio=0.7,
            safety_margin_ratio=0.9,
            minimum_replicas=1,
            maximum_replicas=10,
            maximum_scale_up_step=3,
            maximum_scale_down_step=1,
            capacity_source="test",
            capacity_evidence_reference="C:/absolute/path/data.json",
            workload_profile="test",
        )


def test_reject_unknown_fields():
    with pytest.raises(ValidationError):
        CapacityConfig(
            capacity_requests_per_second_per_replica=25.0,
            target_utilization_ratio=0.7,
            safety_margin_ratio=0.9,
            minimum_replicas=1,
            maximum_replicas=10,
            maximum_scale_up_step=3,
            maximum_scale_down_step=1,
            capacity_source="test",
            capacity_evidence_reference="test.json",
            workload_profile="test",
            unknown_arbitrary_field="forbidden",  # type: ignore[call-arg]
        )


def test_reject_boolean_as_numbers():
    with pytest.raises(ValidationError, match="Boolean values are not accepted"):
        CapacityConfig(
            capacity_requests_per_second_per_replica=True,  # type: ignore[arg-type]
            target_utilization_ratio=0.7,
            safety_margin_ratio=0.9,
            minimum_replicas=1,
            maximum_replicas=10,
            maximum_scale_up_step=3,
            maximum_scale_down_step=1,
            capacity_source="test",
            capacity_evidence_reference="test.json",
            workload_profile="test",
        )

    with pytest.raises(ValidationError, match="Boolean values are not accepted"):
        CapacityConfig(
            capacity_requests_per_second_per_replica=25.0,
            target_utilization_ratio=0.7,
            safety_margin_ratio=0.9,
            minimum_replicas=True,  # type: ignore[arg-type]
            maximum_replicas=10,
            maximum_scale_up_step=3,
            maximum_scale_down_step=1,
            capacity_source="test",
            capacity_evidence_reference="test.json",
            workload_profile="test",
        )
