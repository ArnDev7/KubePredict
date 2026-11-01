"""Forecast Trust Guard configuration for KubePredict controller (Phase 10A).

Provides validated configuration for evaluating forecast trust before allowing
forecast-based autoscaling recommendations to be considered.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from shared.enums import PrimaryErrorMetric

VALID_TRUST_GUARD_STATUSES: set[str] = {
    "provisional_validated",
    "provisional_configured",
}


class TrustGuardConfig(BaseModel):
    """Configuration specification for the Forecast Trust Guard."""

    schema_version: str = "1.0.0"
    enabled: bool = True
    evidence_window_seconds: float = Field(gt=0.0)
    minimum_matched_residuals: int = Field(gt=0)
    residual_max_age_seconds: float = Field(gt=0.0)
    absolute_error_threshold: float = Field(ge=0.0)
    relative_error_threshold: float = Field(ge=0.0)
    primary_error_metric: PrimaryErrorMetric = PrimaryErrorMetric.MAE
    require_fresh_telemetry: bool = True
    require_valid_forecast: bool = True
    minimum_history_observations: int = Field(gt=0)
    threshold_source: str = Field(min_length=1)
    threshold_evidence_reference: str = Field(min_length=1)
    status: str = "provisional_validated"

    model_config = {"extra": "forbid"}

    @field_validator(
        "evidence_window_seconds",
        "minimum_matched_residuals",
        "residual_max_age_seconds",
        "absolute_error_threshold",
        "relative_error_threshold",
        "minimum_history_observations",
        mode="before",
    )
    @classmethod
    def reject_bool_as_number(cls, v: Any) -> Any:
        """Prevent Pydantic from implicitly accepting booleans as numbers."""
        if isinstance(v, bool):
            raise ValueError("Boolean values are not accepted for numeric settings")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        trimmed = v.strip()
        if trimmed not in VALID_TRUST_GUARD_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(VALID_TRUST_GUARD_STATUSES)}, got '{v}' "
                f"('production_ready' is strictly forbidden in Phase 10A)"
            )
        return trimmed

    @field_validator("threshold_source")
    @classmethod
    def validate_non_empty_strings(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("threshold_source cannot be empty or whitespace only")
        return trimmed

    @field_validator("threshold_evidence_reference")
    @classmethod
    def validate_evidence_reference(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("threshold_evidence_reference cannot be empty")
        for part in trimmed.split(";"):
            clean_part = part.split("#")[0].strip()
            if clean_part:
                p = Path(clean_part)
                if p.is_absolute():
                    raise ValueError(
                        f"threshold_evidence_reference must contain relative paths, got absolute '{part}'"
                    )
        return trimmed

    @model_validator(mode="after")
    def validate_relationships(self) -> "TrustGuardConfig":
        if self.residual_max_age_seconds > self.evidence_window_seconds:
            # Although allowed in loose configs, residual_max_age should typically be <= evidence_window
            pass
        return self


def load_trust_guard_config(config_path: str | Path) -> TrustGuardConfig:
    """Load and validate trust guard configuration from YAML.

    Parameters
    ----------
    config_path : str | Path
        Path to the YAML configuration file.

    Returns
    -------
    TrustGuardConfig
        Validated configuration model.
    """
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Trust guard configuration file not found at: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}, got {type(data).__name__}")

    return TrustGuardConfig.model_validate(data)
