"""Unit tests for PolicyConfig (Phase 10B)."""

import pytest
from pydantic import ValidationError

from controller.policy_config import PolicyConfig, load_policy_config


class TestPolicyConfig:
    def test_valid_configuration(self) -> None:
        cfg = PolicyConfig(
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
            source="test_policy_derivation",
            evidence_reference="controller/config/capacity.yaml;controller/config/trust_guard.yaml",
            status="provisional_validated",
        )
        assert cfg.schema_version == "1.0.0"
        assert cfg.request_rate_window_samples == 3
        assert cfg.rapid_change_growth_ratio == 2.0
        assert cfg.reactive_cpu_target_ratio == 0.70

    def test_window_samples_at_least_2(self) -> None:
        with pytest.raises(ValidationError):
            PolicyConfig(
                request_rate_window_samples=1,  # Invalid: must be >= 2
                rapid_change_growth_ratio=2.0,
                rapid_change_absolute_rate=5.0,
                minimum_valid_samples=1,
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

    def test_growth_ratio_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            PolicyConfig(
                request_rate_window_samples=3,
                rapid_change_growth_ratio=0.0,  # Invalid: must be > 0
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

    def test_cpu_target_bounds(self) -> None:
        # <= 0 invalid
        with pytest.raises(ValidationError):
            PolicyConfig(
                request_rate_window_samples=3,
                rapid_change_growth_ratio=2.0,
                rapid_change_absolute_rate=5.0,
                minimum_valid_samples=2,
                reactive_cpu_target_ratio=0.0,
                minimum_replicas=1,
                maximum_replicas=10,
                maximum_scale_up_step=3,
                maximum_scale_down_step=1,
                cooldown_seconds=60.0,
                downscale_stabilization_seconds=90.0,
                source="test",
                evidence_reference="rel/path.yaml",
            )
        # > 1 invalid
        with pytest.raises(ValidationError):
            PolicyConfig(
                request_rate_window_samples=3,
                rapid_change_growth_ratio=2.0,
                rapid_change_absolute_rate=5.0,
                minimum_valid_samples=2,
                reactive_cpu_target_ratio=1.5,
                minimum_replicas=1,
                maximum_replicas=10,
                maximum_scale_up_step=3,
                maximum_scale_down_step=1,
                cooldown_seconds=60.0,
                downscale_stabilization_seconds=90.0,
                source="test",
                evidence_reference="rel/path.yaml",
            )

    def test_minimum_replicas_exceeding_maximum_rejected(self) -> None:
        with pytest.raises(ValidationError, match="minimum_replicas"):
            PolicyConfig(
                request_rate_window_samples=3,
                rapid_change_growth_ratio=2.0,
                rapid_change_absolute_rate=5.0,
                minimum_valid_samples=2,
                reactive_cpu_target_ratio=0.70,
                minimum_replicas=10,
                maximum_replicas=5,  # Invalid: min > max
                maximum_scale_up_step=3,
                maximum_scale_down_step=1,
                cooldown_seconds=60.0,
                downscale_stabilization_seconds=90.0,
                source="test",
                evidence_reference="rel/path.yaml",
            )

    def test_booleans_rejected_as_numbers(self) -> None:
        with pytest.raises(ValidationError, match="Boolean values are not accepted"):
            PolicyConfig(
                request_rate_window_samples=True,  # type: ignore[arg-type]
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

    def test_unknown_fields_rejected(self) -> None:
        raw = {
            "schema_version": "1.0",
            "request_rate_window_samples": 3,
            "rapid_change_growth_ratio": 2.0,
            "rapid_change_absolute_rate": 5.0,
            "minimum_valid_samples": 2,
            "reactive_cpu_target_ratio": 0.70,
            "minimum_replicas": 1,
            "maximum_replicas": 10,
            "maximum_scale_up_step": 3,
            "maximum_scale_down_step": 1,
            "cooldown_seconds": 60.0,
            "downscale_stabilization_seconds": 90.0,
            "source": "test",
            "evidence_reference": "rel/path.yaml",
            "live_scaling_enabled": True,  # Unknown field
        }
        with pytest.raises(ValidationError):
            PolicyConfig.model_validate(raw)

    def test_production_ready_status_forbidden(self) -> None:
        with pytest.raises(ValidationError, match="production_ready"):
            PolicyConfig(
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
                status="production_ready",
            )

    def test_absolute_path_rejection(self) -> None:
        with pytest.raises(ValidationError, match="must contain relative paths"):
            PolicyConfig(
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
                evidence_reference="C:\\Users\\arnav\\Desktop\\test.yaml",
            )

    def test_load_policy_config_from_yaml(self) -> None:
        cfg = load_policy_config("controller/config/policy.yaml")
        assert cfg.status == "provisional_validated"
        assert cfg.minimum_replicas == 1
        assert cfg.maximum_replicas == 10
        assert cfg.maximum_scale_up_step == 3
        assert cfg.maximum_scale_down_step == 1
        assert cfg.cooldown_seconds == 60.0
