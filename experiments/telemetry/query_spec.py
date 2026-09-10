"""Query specification loader and validator."""

from hashlib import sha256
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class QueryDefinition(BaseModel):
    """Specification for a single telemetry metric query."""

    field_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source: Literal["prometheus"] = "prometheus"
    promql: str = Field(min_length=1)
    expected_result_type: Literal["matrix", "vector"] = "matrix"
    aggregation: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    required: bool = True

    model_config = {"extra": "forbid"}

    @field_validator("promql")
    @classmethod
    def validate_promql_syntax(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("promql query cannot be empty")
        return trimmed


class TelemetryQueryConfig(BaseModel):
    """Collection of system and pod-level telemetry queries."""

    version: str = Field(default="1.0")
    step: str = Field(default="15s")
    queries: list[QueryDefinition] = Field(min_length=1)
    pod_queries: list[QueryDefinition] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_unique_field_names(self) -> "TelemetryQueryConfig":
        system_fields = set()
        for q in self.queries:
            if q.field_name in system_fields:
                raise ValueError(f"Duplicate system query field name: {q.field_name}")
            system_fields.add(q.field_name)

        pod_fields = set()
        for q in self.pod_queries:
            if q.field_name in pod_fields:
                raise ValueError(f"Duplicate pod query field name: {q.field_name}")
            pod_fields.add(q.field_name)

        return self


def compute_query_spec_hash(path: Path | str) -> str:
    """Compute the SHA-256 hash of the query specification file."""
    p = Path(path)
    content = p.read_bytes()
    return sha256(content).hexdigest()


def load_query_spec(path: Path | str) -> TelemetryQueryConfig:
    """Load and validate the telemetry query specification from YAML."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Query specification file not found: {p}")

    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML structure in {p}: expected dictionary")

    return TelemetryQueryConfig(**data)
