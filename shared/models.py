from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from shared.enums import ControllerMode, DecisionReason, ExperimentMode, ScalingDirection
from shared.validation import require_timezone_aware


class TelemetrySnapshot(BaseModel):
    timestamp: datetime
    request_rate_per_second: float = Field(ge=0.0)
    cpu_utilization_ratio: float = Field(ge=0.0, le=1.0)
    memory_utilization_ratio: float = Field(ge=0.0, le=1.0)
    p95_latency_ms: float = Field(ge=0.0)
    in_progress_requests: int = Field(ge=0)
    queue_depth: int = Field(ge=0)
    ready_replicas: int = Field(ge=0)
    desired_replicas: int = Field(ge=0)
    error_rate_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    source: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_timezone(self) -> "TelemetrySnapshot":
        self.timestamp = require_timezone_aware(self.timestamp)
        return self


class ForecastResult(BaseModel):
    generated_at: datetime
    horizon_seconds: int = Field(gt=0)
    predicted_request_rate_per_second: float = Field(ge=0.0)
    confidence: float = Field(ge=0.0, le=1.0)
    model_name: str = Field(min_length=1)
    observations_used: int = Field(gt=0)
    lower_bound: float | None = Field(default=None, ge=0.0)
    upper_bound: float | None = Field(default=None, ge=0.0)
    validation_error: float | None = Field(default=None, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timezone_and_bounds(self) -> "ForecastResult":
        self.generated_at = require_timezone_aware(self.generated_at)
        if (
            self.lower_bound is not None
            and self.upper_bound is not None
            and self.lower_bound > self.upper_bound
        ):
            raise ValueError("lower_bound must be <= upper_bound")
        if (
            self.lower_bound is not None
            and self.lower_bound > self.predicted_request_rate_per_second
        ):
            raise ValueError("lower_bound must be <= predicted_request_rate_per_second")
        if (
            self.upper_bound is not None
            and self.upper_bound < self.predicted_request_rate_per_second
        ):
            raise ValueError("upper_bound must be >= predicted_request_rate_per_second")
        return self


class ScalingDecision(BaseModel):
    timestamp: datetime
    mode: ControllerMode
    current_replicas: int = Field(ge=0)
    proposed_replicas: int = Field(ge=0)
    final_replicas: int = Field(ge=0)
    direction: ScalingDirection
    reason: DecisionReason
    confidence: float = Field(ge=0.0, le=1.0)
    live_scaling_enabled: bool
    forecast: ForecastResult | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_decision(self) -> "ScalingDecision":
        self.timestamp = require_timezone_aware(self.timestamp)
        if self.live_scaling_enabled and self.mode != ControllerMode.LIVE:
            raise ValueError("live_scaling_enabled can be true only when mode is live")
        if (
            self.final_replicas > self.current_replicas
            and self.direction != ScalingDirection.SCALE_UP
        ):
            raise ValueError("final > current requires scale_up")
        if (
            self.final_replicas < self.current_replicas
            and self.direction != ScalingDirection.SCALE_DOWN
        ):
            raise ValueError("final < current requires scale_down")
        if (
            self.final_replicas == self.current_replicas
            and self.direction != ScalingDirection.NO_CHANGE
        ):
            raise ValueError("final == current requires no_change")
        return self


class ExperimentMetadata(BaseModel):
    experiment_id: str = Field(min_length=1)
    started_at: datetime
    mode: ExperimentMode
    scenario: str = Field(min_length=1)
    random_seed: int
    application_image: str = Field(min_length=1)
    minimum_replicas: int = Field(gt=0)
    maximum_replicas: int = Field(gt=0)
    slo_latency_ms: float = Field(gt=0.0)
    git_commit: str | None = None
    cluster_provider: str | None = None
    cluster_name: str | None = None
    notes: str | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_metadata(self) -> "ExperimentMetadata":
        self.started_at = require_timezone_aware(self.started_at)
        if self.minimum_replicas > self.maximum_replicas:
            raise ValueError("minimum_replicas must be <= maximum_replicas")
        if ":" not in self.application_image and "@sha256:" not in self.application_image:
            raise ValueError("application_image must use an explicit tag or digest")
        if self.application_image.endswith(":latest"):
            raise ValueError("application_image must not use latest tag")
        return self
