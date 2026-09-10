"""Typed configuration model for observe-only controller (Phase 9A)."""

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

FIXED_PROMETHEUS_QUERY: str = 'sum(rate(apa_http_requests_total{job="kubepredict-workload"}[1m]))'
LOCALHOST_HOSTNAMES: set[str] = {"127.0.0.1", "localhost", "::1"}


class ObserverConfig(BaseModel):
    """Configuration for KubePredict observe-only forecasting controller."""

    schema_version: str = "1.0.0"
    mode: str = "observe"
    prometheus_url: str = "http://127.0.0.1:9090"
    query: str = FIXED_PROMETHEUS_QUERY
    sampling_interval_seconds: float = Field(default=15.0, gt=0.0)
    history_window_seconds: float = Field(default=180.0, gt=0.0)
    forecast_interval_seconds: float = Field(default=15.0, gt=0.0)
    forecast_horizon_steps: int = Field(default=1, gt=0)
    telemetry_stale_after_seconds: float = Field(default=30.0, gt=0.0)
    request_timeout_seconds: float = Field(default=10.0, gt=0.0, le=10.0)
    maximum_retries: int = Field(default=3, ge=0, le=3)
    maximum_cycles: int = Field(default=10, gt=0)
    output_root: Path = Field(default_factory=lambda: Path("experiments/output/controller-observe"))
    frozen_forecaster_config: Path = Field(
        default_factory=lambda: Path("forecasting/config/final_forecaster.yaml")
    )
    metrics_host: str = "127.0.0.1"
    metrics_port: int = Field(default=9101, ge=1024, le=65535)

    model_config = {"extra": "forbid"}

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v != "observe":
            raise ValueError(f"controller mode must be strictly 'observe', got '{v}'")
        return v

    @field_validator(
        "sampling_interval_seconds",
        "history_window_seconds",
        "forecast_interval_seconds",
        "forecast_horizon_steps",
        "telemetry_stale_after_seconds",
        "request_timeout_seconds",
        "maximum_retries",
        "maximum_cycles",
        "metrics_port",
        mode="before",
    )
    @classmethod
    def reject_bool_as_number(cls, v: Any) -> Any:
        """Prevent Pydantic from implicitly accepting booleans as numbers."""
        if isinstance(v, bool):
            raise ValueError("Boolean values are not accepted for numeric settings")
        return v

    @field_validator("prometheus_url")
    @classmethod
    def validate_prometheus_url(cls, v: str) -> str:
        trimmed = v.strip().rstrip("/")
        if not trimmed:
            raise ValueError("prometheus_url cannot be empty")
        parsed = urlparse(trimmed)
        hostname = (parsed.hostname or "").lower()
        if hostname not in LOCALHOST_HOSTNAMES:
            raise ValueError(f"prometheus_url must target localhost for Phase 9A; got '{hostname}'")
        return trimmed

    @field_validator("query")
    @classmethod
    def validate_fixed_query(cls, v: str) -> str:
        trimmed = v.strip()
        if trimmed != FIXED_PROMETHEUS_QUERY:
            raise ValueError(
                f"query must match the fixed version-controlled PromQL query: '{FIXED_PROMETHEUS_QUERY}'; "
                f"arbitrary PromQL is forbidden in Phase 9A"
            )
        return trimmed

    @field_validator("metrics_host")
    @classmethod
    def validate_metrics_host(cls, v: str) -> str:
        trimmed = v.strip()
        if trimmed not in LOCALHOST_HOSTNAMES:
            raise ValueError(f"metrics_host must be localhost; got '{trimmed}'")
        return trimmed

    @model_validator(mode="after")
    def validate_relationships(self) -> "ObserverConfig":
        if self.mode != "observe":
            raise ValueError(f"controller mode must be strictly 'observe', got '{self.mode}'")

        if self.history_window_seconds < self.sampling_interval_seconds:
            raise ValueError(
                f"history_window_seconds ({self.history_window_seconds}) must be >= "
                f"sampling_interval_seconds ({self.sampling_interval_seconds})"
            )

        # Validate against frozen configuration if present
        if self.frozen_forecaster_config.exists():
            try:
                raw_cfg = yaml.safe_load(self.frozen_forecaster_config.read_text(encoding="utf-8"))
                if isinstance(raw_cfg, dict):
                    exp_h = raw_cfg.get("default_controller_horizon_steps", 1)
                    if self.forecast_horizon_steps != exp_h:
                        raise ValueError(
                            f"forecast_horizon_steps ({self.forecast_horizon_steps}) does not match "
                            f"frozen forecaster default horizon ({exp_h})"
                        )
            except Exception as e:
                if isinstance(e, ValueError):
                    raise
                # File reading error or unparseable YAML

        return self

    @classmethod
    def from_yaml_file(cls, path: str | Path) -> "ObserverConfig":
        """Load observer configuration from a YAML file."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Configuration file not found: {p}")
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Configuration file {p} must contain a YAML mapping")
        return cls(**data)
