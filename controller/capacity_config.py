"""Capacity model configuration for KubePredict controller (Phase 9B)."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

VALID_CAPACITY_STATUSES: set[str] = {
    "provisional_calibrated",
    "provisional_configured",
}


class CapacityConfig(BaseModel):
    """Configuration specification for the transparent capacity model."""

    schema_version: str = "1.0.0"
    status: str = "provisional_calibrated"
    capacity_requests_per_second_per_replica: float = Field(gt=0.0)
    unit: str = "requests_per_second"
    target_utilization_ratio: float = Field(gt=0.0, le=1.0)
    safety_margin_ratio: float = Field(gt=0.0, le=1.0)
    minimum_replicas: int = Field(gt=0)
    maximum_replicas: int = Field(gt=0)
    maximum_scale_up_step: int = Field(gt=0)
    maximum_scale_down_step: int = Field(gt=0)
    minimum_prediction_rate: float = Field(default=0.0, ge=0.0)
    capacity_source: str = Field(min_length=1)
    capacity_evidence_reference: str = Field(min_length=1)
    workload_profile: str = Field(min_length=1)

    model_config = {"extra": "forbid"}

    @field_validator(
        "capacity_requests_per_second_per_replica",
        "target_utilization_ratio",
        "safety_margin_ratio",
        "minimum_replicas",
        "maximum_replicas",
        "maximum_scale_up_step",
        "maximum_scale_down_step",
        "minimum_prediction_rate",
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
        if trimmed not in VALID_CAPACITY_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(VALID_CAPACITY_STATUSES)}, got '{v}' "
                f"('production_ready' is strictly forbidden in Phase 9B)"
            )
        return trimmed

    @field_validator("capacity_source", "workload_profile", "unit")
    @classmethod
    def validate_non_empty_strings(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("String field cannot be empty or whitespace only")
        return trimmed

    @field_validator("capacity_evidence_reference")
    @classmethod
    def validate_evidence_reference(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("capacity_evidence_reference cannot be empty")
        p = Path(trimmed)
        if p.is_absolute():
            raise ValueError(
                f"capacity_evidence_reference must be a relative repository path, got absolute '{trimmed}'"
            )
        return trimmed

    @model_validator(mode="after")
    def validate_relationships(self) -> "CapacityConfig":
        if self.minimum_replicas > self.maximum_replicas:
            raise ValueError(
                f"minimum_replicas ({self.minimum_replicas}) must be <= maximum_replicas ({self.maximum_replicas})"
            )
        return self

    @property
    def effective_capacity_per_replica(self) -> float:
        """Compute effective per-replica throughput capacity.

        Formula:
        effective_capacity = capacity_requests_per_second_per_replica * target_utilization_ratio * safety_margin_ratio
        """
        eff = (
            self.capacity_requests_per_second_per_replica
            * self.target_utilization_ratio
            * self.safety_margin_ratio
        )
        return round(eff, 4)

    @classmethod
    def from_yaml_file(cls, path: str | Path) -> "CapacityConfig":
        """Load and validate capacity configuration from a YAML file."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Capacity configuration file not found: {p}")
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Configuration file {p} must contain a YAML mapping")
        return cls(**data)


def load_capacity_config(path: str | Path) -> CapacityConfig:
    """Convenience function to load and validate capacity configuration."""
    return CapacityConfig.from_yaml_file(path)
