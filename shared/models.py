from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from shared.enums import (
    ControllerMode,
    DecisionReason,
    ExperimentMode,
    ScalingDirection,
    TrustOutcome,
)
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
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    live_scaling_enabled: bool = False
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


class ForecastTrustDecision(BaseModel):
    """Deterministic, auditable trust decision on a forecast for autoscaling eligibility (Phase 10A)."""

    evaluated_at: datetime
    trusted: bool
    outcome: TrustOutcome
    forecast_id: str | None = None
    model_name: str = Field(min_length=1)
    forecast_generated_at: datetime | None = None
    forecast_target_timestamp: datetime | None = None
    telemetry_latest_timestamp: datetime | None = None
    telemetry_age_seconds: float | None = Field(default=None, ge=0.0)
    telemetry_fresh: bool
    history_observations: int = Field(ge=0)
    minimum_history_required: int = Field(ge=0)
    matched_residual_count: int = Field(ge=0)
    minimum_matched_residuals: int = Field(ge=0)
    residual_window_start: datetime | None = None
    residual_window_end: datetime | None = None
    recent_mae: float | None = Field(default=None, ge=0.0)
    recent_rmse: float | None = Field(default=None, ge=0.0)
    recent_smape: float | None = Field(default=None, ge=0.0)
    recent_bias: float | None = None
    absolute_error_threshold: float = Field(ge=0.0)
    relative_error_threshold: float = Field(ge=0.0)
    evidence_window_seconds: float = Field(gt=0.0)
    warnings: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_trust_decision(self) -> "ForecastTrustDecision":
        self.evaluated_at = require_timezone_aware(self.evaluated_at)
        if self.forecast_generated_at is not None:
            self.forecast_generated_at = require_timezone_aware(self.forecast_generated_at)
        if self.forecast_target_timestamp is not None:
            self.forecast_target_timestamp = require_timezone_aware(self.forecast_target_timestamp)
        if self.telemetry_latest_timestamp is not None:
            self.telemetry_latest_timestamp = require_timezone_aware(
                self.telemetry_latest_timestamp
            )
        if self.residual_window_start is not None:
            self.residual_window_start = require_timezone_aware(self.residual_window_start)
        if self.residual_window_end is not None:
            self.residual_window_end = require_timezone_aware(self.residual_window_end)

        # Consistency checks between trusted and outcome
        if self.trusted:
            if self.outcome != TrustOutcome.TRUSTED:
                raise ValueError("trusted=true requires outcome='trusted'")
            if not self.telemetry_fresh:
                raise ValueError("trusted=true requires telemetry_fresh=true")
            if self.history_observations < self.minimum_history_required:
                raise ValueError("trusted=true requires sufficient history observations")
            if self.matched_residual_count < self.minimum_matched_residuals:
                raise ValueError("trusted=true requires sufficient matched residuals")
            if self.forecast_id is None:
                raise ValueError("trusted=true requires a valid forecast_id")
            if self.recent_mae is None or self.recent_smape is None:
                raise ValueError("trusted=true requires calculated recent error metrics")
            if self.recent_mae > self.absolute_error_threshold:
                raise ValueError("trusted=true requires recent_mae <= absolute_error_threshold")
            if self.recent_smape > self.relative_error_threshold:
                raise ValueError("trusted=true requires recent_smape <= relative_error_threshold")
        else:
            if self.outcome == TrustOutcome.TRUSTED:
                raise ValueError("trusted=false cannot have outcome='trusted'")

        # When residual evidence is insufficient, error metrics should not be fabricated
        if self.matched_residual_count < self.minimum_matched_residuals:
            if any(
                m is not None
                for m in (self.recent_mae, self.recent_rmse, self.recent_smape, self.recent_bias)
            ):
                raise ValueError(
                    "recent metrics must be None when matched_residual_count < minimum_matched_residuals"
                )

        return self
