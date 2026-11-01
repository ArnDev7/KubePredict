"""Unit tests for TrustGuardConfig (Phase 10A)."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from controller.trust_config import TrustGuardConfig, load_trust_guard_config
from shared.enums import PrimaryErrorMetric


def get_valid_trust_config_dict() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "status": "provisional_validated",
        "enabled": True,
        "evidence_window_seconds": 180.0,
        "minimum_matched_residuals": 3,
        "residual_max_age_seconds": 120.0,
        "absolute_error_threshold": 10.0,
        "relative_error_threshold": 50.0,
        "primary_error_metric": "mae",
        "require_fresh_telemetry": True,
        "require_valid_forecast": True,
        "minimum_history_observations": 1,
        "threshold_source": "offline_scenario_validation_and_live_baseline_residuals",
        "threshold_evidence_reference": "forecasting/config/final_forecaster.yaml#L70-L79;experiments/output/controller-dry-run/dry-run-20261027_105511/matched_residuals.csv",
    }


class TestTrustGuardConfig:
    """Validate TrustGuardConfig model constraints and loader."""

    def test_valid_configuration(self) -> None:
        cfg = TrustGuardConfig(**get_valid_trust_config_dict())
        assert cfg.enabled is True
        assert cfg.evidence_window_seconds == 180.0
        assert cfg.minimum_matched_residuals == 3
        assert cfg.residual_max_age_seconds == 120.0
        assert cfg.absolute_error_threshold == 10.0
        assert cfg.relative_error_threshold == 50.0
        assert cfg.primary_error_metric == PrimaryErrorMetric.MAE
        assert cfg.status == "provisional_validated"

    def test_disabled_behavior_allowed(self) -> None:
        d = get_valid_trust_config_dict()
        d["enabled"] = False
        cfg = TrustGuardConfig(**d)
        assert cfg.enabled is False

    def test_zero_or_negative_window_rejected(self) -> None:
        d = get_valid_trust_config_dict()
        d["evidence_window_seconds"] = 0.0
        with pytest.raises(ValidationError):
            TrustGuardConfig(**d)

        d["evidence_window_seconds"] = -10.0
        with pytest.raises(ValidationError):
            TrustGuardConfig(**d)

    def test_zero_residual_minimum_rejected(self) -> None:
        d = get_valid_trust_config_dict()
        d["minimum_matched_residuals"] = 0
        with pytest.raises(ValidationError):
            TrustGuardConfig(**d)

    def test_negative_thresholds_rejected(self) -> None:
        d = get_valid_trust_config_dict()
        d["absolute_error_threshold"] = -1.0
        with pytest.raises(ValidationError):
            TrustGuardConfig(**d)

        d["absolute_error_threshold"] = 10.0
        d["relative_error_threshold"] = -0.5
        with pytest.raises(ValidationError):
            TrustGuardConfig(**d)

    def test_invalid_primary_metric_rejected(self) -> None:
        d = get_valid_trust_config_dict()
        d["primary_error_metric"] = "invalid_metric"
        with pytest.raises(ValidationError):
            TrustGuardConfig(**d)

    def test_blank_source_rejected(self) -> None:
        d = get_valid_trust_config_dict()
        d["threshold_source"] = "   "
        with pytest.raises(ValidationError):
            TrustGuardConfig(**d)

    def test_blank_evidence_reference_rejected(self) -> None:
        d = get_valid_trust_config_dict()
        d["threshold_evidence_reference"] = "   "
        with pytest.raises(ValidationError):
            TrustGuardConfig(**d)

    def test_unknown_fields_rejected(self) -> None:
        d = get_valid_trust_config_dict()
        d["live_scaling_enabled"] = True
        with pytest.raises(ValidationError):
            TrustGuardConfig(**d)

    def test_booleans_as_numbers_rejected(self) -> None:
        d = get_valid_trust_config_dict()
        d["minimum_matched_residuals"] = True
        with pytest.raises(ValidationError):
            TrustGuardConfig(**d)

        d = get_valid_trust_config_dict()
        d["absolute_error_threshold"] = False
        with pytest.raises(ValidationError):
            TrustGuardConfig(**d)

    def test_absolute_path_rejection(self) -> None:
        d = get_valid_trust_config_dict()
        d["threshold_evidence_reference"] = "C:\\Users\\arnav\\Desktop\\test.yaml"
        with pytest.raises(ValidationError, match="relative"):
            TrustGuardConfig(**d)

    def test_invalid_status_rejected(self) -> None:
        d = get_valid_trust_config_dict()
        d["status"] = "production_ready"
        with pytest.raises(ValidationError, match="production_ready"):
            TrustGuardConfig(**d)

        d["status"] = "arbitrary_status"
        with pytest.raises(ValidationError):
            TrustGuardConfig(**d)

    def test_load_trust_guard_config_from_yaml(self, tmp_path: Path) -> None:
        yaml_content = """
schema_version: "1.0.0"
status: "provisional_validated"
enabled: true
evidence_window_seconds: 180.0
minimum_matched_residuals: 3
residual_max_age_seconds: 120.0
absolute_error_threshold: 10.0
relative_error_threshold: 50.0
primary_error_metric: "mae"
require_fresh_telemetry: true
require_valid_forecast: true
minimum_history_observations: 1
threshold_source: "offline_scenario_validation_and_live_baseline_residuals"
threshold_evidence_reference: "forecasting/config/final_forecaster.yaml#L70-L79"
"""
        yaml_file = tmp_path / "test_trust_guard.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        cfg = load_trust_guard_config(yaml_file)
        assert cfg.enabled is True
        assert cfg.minimum_matched_residuals == 3

    def test_load_nonexistent_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_trust_guard_config(tmp_path / "missing.yaml")
