"""Policy configuration for KubePredict controller dry-run arbitration (Phase 10B).

Provides validated configuration for rapid-change detection, reactive fallback,
arbitration policy, replica bounds, step limits, cooldown, and downscale stabilization.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

VALID_POLICY_STATUSES: set[str] = {
    "provisional_validated",
    "provisional_configured",
}


class PolicyConfig(BaseModel):
    """Configuration specification for KubePredict arbitration policy."""

    schema_version: str = "1.0.0"
    status: str = "provisional_validated"

    # Rapid-change detection
    request_rate_window_samples: int = Field(ge=2)
    rapid_change_growth_ratio: float = Field(gt=0.0)
    rapid_change_absolute_rate: float = Field(ge=0.0)
    minimum_valid_samples: int = Field(gt=0)

    # Reactive fallback
    reactive_cpu_target_ratio: float = Field(gt=0.0, le=1.0)

    # Replica bounds
    minimum_replicas: int = Field(gt=0)
    maximum_replicas: int = Field(gt=0)

    # Step limits
    maximum_scale_up_step: int = Field(gt=0)
    maximum_scale_down_step: int = Field(gt=0)

    # Cooldown & stabilization
    cooldown_seconds: float = Field(ge=0.0)
    downscale_stabilization_seconds: float = Field(ge=0.0)

    # Provenance
    source: str = Field(min_length=1)
    evidence_reference: str = Field(min_length=1)

    model_config = {"extra": "forbid"}

    @field_validator(
        "request_rate_window_samples",
        "rapid_change_growth_ratio",
        "rapid_change_absolute_rate",
        "minimum_valid_samples",
        "reactive_cpu_target_ratio",
        "minimum_replicas",
        "maximum_replicas",
        "maximum_scale_up_step",
        "maximum_scale_down_step",
        "cooldown_seconds",
        "downscale_stabilization_seconds",
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
        if trimmed not in VALID_POLICY_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(VALID_POLICY_STATUSES)}, got '{v}' "
                f"('production_ready' is strictly forbidden in Phase 10B)"
            )
        return trimmed

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("source cannot be empty or whitespace only")
        return trimmed

    @field_validator("evidence_reference")
    @classmethod
    def validate_evidence_reference(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("evidence_reference cannot be empty")
        for part in trimmed.split(";"):
            clean_part = part.split("#")[0].strip()
            if clean_part:
                p = Path(clean_part)
                if p.is_absolute() or (len(clean_part) > 1 and clean_part[1] == ":"):
                    raise ValueError(
                        f"evidence_reference must contain relative paths, got absolute '{part}'"
                    )
        return trimmed

    @model_validator(mode="after")
    def validate_relationships(self) -> "PolicyConfig":
        if self.minimum_replicas > self.maximum_replicas:
            raise ValueError(
                f"minimum_replicas ({self.minimum_replicas}) cannot exceed maximum_replicas ({self.maximum_replicas})"
            )
        if self.minimum_valid_samples > self.request_rate_window_samples:
            raise ValueError(
                f"minimum_valid_samples ({self.minimum_valid_samples}) cannot exceed request_rate_window_samples ({self.request_rate_window_samples})"
            )
        return self


def load_policy_config(config_path: str | Path) -> PolicyConfig:
    """Load and validate arbitration policy configuration from YAML.

    Parameters
    ----------
    config_path : str | Path
        Path to the YAML configuration file.

    Returns
    -------
    PolicyConfig
        Validated configuration model.
    """
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Policy configuration file not found at: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}, got {type(data).__name__}")

    return PolicyConfig.model_validate(data)
