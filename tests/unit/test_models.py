from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from shared.enums import ControllerMode, DecisionReason, ExperimentMode, ScalingDirection
from shared.models import ExperimentMetadata, ForecastResult, ScalingDecision, TelemetrySnapshot


def test_telemetry_snapshot_valid():
    dt = datetime.now(UTC)
    ts = TelemetrySnapshot(
        timestamp=dt,
        request_rate_per_second=100.5,
        cpu_utilization_ratio=0.75,
        memory_utilization_ratio=0.60,
        p95_latency_ms=120.0,
        in_progress_requests=10,
        queue_depth=5,
        ready_replicas=5,
        desired_replicas=3,  # desired < ready is valid transient state
    )
    assert ts.timestamp == dt
    assert ts.desired_replicas < ts.ready_replicas


def test_telemetry_snapshot_naive_dt():
    with pytest.raises(ValidationError, match="timezone-aware"):
        TelemetrySnapshot(
            timestamp=datetime.now(),  # naive
            request_rate_per_second=100.5,
            cpu_utilization_ratio=0.75,
            memory_utilization_ratio=0.60,
            p95_latency_ms=120.0,
            in_progress_requests=10,
            queue_depth=5,
            ready_replicas=5,
            desired_replicas=5,
        )


def test_telemetry_snapshot_negative_rate():
    dt = datetime.now(UTC)
    with pytest.raises(ValidationError):
        TelemetrySnapshot(
            timestamp=dt,
            request_rate_per_second=-1.0,
            cpu_utilization_ratio=0.75,
            memory_utilization_ratio=0.60,
            p95_latency_ms=120.0,
            in_progress_requests=10,
            queue_depth=5,
            ready_replicas=5,
            desired_replicas=5,
        )


def test_forecast_result_valid():
    dt = datetime.now(UTC)
    fr = ForecastResult(
        generated_at=dt,
        horizon_seconds=60,
        predicted_request_rate_per_second=150.0,
        confidence=0.9,
        model_name="test_model",
        observations_used=100,
    )
    assert fr.confidence == 0.9


def test_forecast_result_bounds():
    dt = datetime.now(UTC)
    with pytest.raises(ValidationError, match="lower_bound must be <= predicted"):
        ForecastResult(
            generated_at=dt,
            horizon_seconds=60,
            predicted_request_rate_per_second=150.0,
            confidence=0.9,
            model_name="test",
            observations_used=10,
            lower_bound=160.0,
        )
    with pytest.raises(ValidationError, match="upper_bound must be >= predicted"):
        ForecastResult(
            generated_at=dt,
            horizon_seconds=60,
            predicted_request_rate_per_second=150.0,
            confidence=0.9,
            model_name="test",
            observations_used=10,
            upper_bound=140.0,
        )
    with pytest.raises(ValidationError, match="lower_bound must be <= upper_bound"):
        ForecastResult(
            generated_at=dt,
            horizon_seconds=60,
            predicted_request_rate_per_second=150.0,
            confidence=0.9,
            model_name="test",
            observations_used=10,
            lower_bound=150.0,
            upper_bound=140.0,
        )


def test_scaling_decision_valid():
    dt = datetime.now(UTC)
    sd = ScalingDecision(
        timestamp=dt,
        mode=ControllerMode.LIVE,
        current_replicas=3,
        proposed_replicas=5,
        final_replicas=5,
        direction=ScalingDirection.SCALE_UP,
        reason=DecisionReason.FORECAST_DEMAND,
        confidence=0.8,
        live_scaling_enabled=True,
    )
    assert sd.final_replicas == 5


def test_scaling_decision_invalid_direction():
    dt = datetime.now(UTC)
    with pytest.raises(ValidationError, match="final > current requires scale_up"):
        ScalingDecision(
            timestamp=dt,
            mode=ControllerMode.OBSERVE,
            current_replicas=3,
            proposed_replicas=5,
            final_replicas=5,
            direction=ScalingDirection.NO_CHANGE,
            reason=DecisionReason.FORECAST_DEMAND,
            confidence=0.8,
            live_scaling_enabled=False,
        )


def test_scaling_decision_live_scaling():
    dt = datetime.now(UTC)
    with pytest.raises(
        ValidationError, match="live_scaling_enabled can be true only when mode is live"
    ):
        ScalingDecision(
            timestamp=dt,
            mode=ControllerMode.OBSERVE,
            current_replicas=3,
            proposed_replicas=3,
            final_replicas=3,
            direction=ScalingDirection.NO_CHANGE,
            reason=DecisionReason.NO_CHANGE_REQUIRED,
            confidence=0.8,
            live_scaling_enabled=True,
        )


def test_experiment_metadata_valid():
    dt = datetime.now(UTC)
    em = ExperimentMetadata(
        experiment_id="exp-001",
        started_at=dt,
        mode=ExperimentMode.GUARDED_PREDICTIVE,
        scenario="burst",
        random_seed=42,
        application_image="workload-api:0.1.0",
        minimum_replicas=1,
        maximum_replicas=10,
        slo_latency_ms=500.0,
    )
    assert em.experiment_id == "exp-001"


def test_experiment_metadata_invalid_image():
    dt = datetime.now(UTC)
    with pytest.raises(ValidationError, match="explicit tag or digest"):
        ExperimentMetadata(
            experiment_id="exp",
            started_at=dt,
            mode=ExperimentMode.FIXED,
            scenario="test",
            random_seed=1,
            application_image="workload-api",
            minimum_replicas=1,
            maximum_replicas=10,
            slo_latency_ms=500.0,
        )
    with pytest.raises(ValidationError, match="must not use latest tag"):
        ExperimentMetadata(
            experiment_id="exp",
            started_at=dt,
            mode=ExperimentMode.FIXED,
            scenario="test",
            random_seed=1,
            application_image="workload-api:latest",
            minimum_replicas=1,
            maximum_replicas=10,
            slo_latency_ms=500.0,
        )
