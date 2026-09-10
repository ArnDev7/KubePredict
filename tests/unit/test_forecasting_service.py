"""Unit tests for ForecastingService runtime contracts and execution (Phase 8B)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from forecasting.base import ForecastInput, ForecastOutput
from forecasting.service import ForecastingService, instantiate_forecaster


def _make_valid_input(
    n_points: int = 5,
    interval: float = 15.0,
    requested_horizon: int = 1,
    base_val: float = 50.0,
) -> ForecastInput:
    now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
    timestamps = [now - timedelta(seconds=(n_points - 1 - i) * interval) for i in range(n_points)]
    observations = [base_val + float(i) for i in range(n_points)]
    return ForecastInput(
        generated_at=now,
        sampling_interval_seconds=interval,
        observations=observations,
        observation_timestamps=timestamps,
        requested_horizon_steps=requested_horizon,
    )


class TestForecastInputContract:
    """Validate ForecastInput schema and constraints."""

    def test_valid_input_creation(self) -> None:
        inp = _make_valid_input()
        assert len(inp.observations) == 5
        assert inp.sampling_interval_seconds == 15.0
        assert inp.requested_horizon_steps == 1

    def test_rejects_timezone_naive(self) -> None:
        naive_now = datetime(2026, 9, 10, 12, 0, 0)  # noqa: DTZ001
        with pytest.raises(ValueError, match="timezone-aware"):
            ForecastInput(
                generated_at=naive_now,
                sampling_interval_seconds=15.0,
                observations=[10.0],
                observation_timestamps=[naive_now],
                requested_horizon_steps=1,
            )

    def test_rejects_mismatched_lengths(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValidationError, match="Length of observations"):
            ForecastInput(
                generated_at=now,
                sampling_interval_seconds=15.0,
                observations=[10.0, 20.0],
                observation_timestamps=[now],
                requested_horizon_steps=1,
            )

    def test_rejects_empty_observations(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValidationError, match="observations must not be empty"):
            ForecastInput(
                generated_at=now,
                sampling_interval_seconds=15.0,
                observations=[],
                observation_timestamps=[],
                requested_horizon_steps=1,
            )

    def test_rejects_non_chronological_timestamps(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        earlier = now - timedelta(seconds=15)
        with pytest.raises(ValidationError, match="strictly chronological"):
            ForecastInput(
                generated_at=now,
                sampling_interval_seconds=15.0,
                observations=[10.0, 20.0],
                observation_timestamps=[now, earlier],
                requested_horizon_steps=1,
            )

    def test_rejects_negative_observation(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValidationError, match="non-negative"):
            ForecastInput(
                generated_at=now,
                sampling_interval_seconds=15.0,
                observations=[-5.0],
                observation_timestamps=[now],
                requested_horizon_steps=1,
            )

    def test_rejects_non_finite_observation(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValidationError, match="finite"):
            ForecastInput(
                generated_at=now,
                sampling_interval_seconds=15.0,
                observations=[float("nan")],
                observation_timestamps=[now],
                requested_horizon_steps=1,
            )


class TestForecastOutputContract:
    """Validate ForecastOutput schema and constraints."""

    def test_valid_output_creation(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        out = ForecastOutput(
            generated_at=now,
            model_name="persistence",
            model_version="1.0.0",
            horizon_steps=2,
            horizon_seconds=30.0,
            predicted_values=[25.0, 25.0],
            predicted_request_rate_per_second=25.0,
            confidence_inputs={"history_count": 5},
            history_count=5,
            fallback_used=False,
            warnings=[],
        )
        assert out.horizon_steps == 2
        assert out.predicted_request_rate_per_second == 25.0

    def test_rejects_mismatched_prediction_length(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValidationError, match="predicted_values length"):
            ForecastOutput(
                generated_at=now,
                model_name="persistence",
                model_version="1.0.0",
                horizon_steps=2,
                horizon_seconds=30.0,
                predicted_values=[25.0],  # only 1 value for horizon_steps=2
                predicted_request_rate_per_second=25.0,
                confidence_inputs={},
                history_count=5,
            )

    def test_rejects_mismatched_scalar_rate(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValidationError, match="must equal the final horizon step"):
            ForecastOutput(
                generated_at=now,
                model_name="persistence",
                model_version="1.0.0",
                horizon_steps=2,
                horizon_seconds=30.0,
                predicted_values=[25.0, 30.0],
                predicted_request_rate_per_second=25.0,  # does not match last element (30.0)
                confidence_inputs={},
                history_count=5,
            )

    def test_rejects_negative_prediction(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValidationError, match="non-negative"):
            ForecastOutput(
                generated_at=now,
                model_name="persistence",
                model_version="1.0.0",
                horizon_steps=1,
                horizon_seconds=15.0,
                predicted_values=[-1.0],
                predicted_request_rate_per_second=0.0,
                confidence_inputs={},
                history_count=5,
            )


class TestForecastingService:
    """Validate pure in-process forecasting service logic."""

    @pytest.fixture
    def frozen_config_dict(self) -> dict:
        return {
            "schema_version": "1.0.0",
            "status": "offline_selected",
            "model_family": "persistence",
            "model_name": "persistence",
            "model_version": "1.0.0",
            "parameters": {},
            "target_column": "request_rate_per_second",
            "sampling_interval_seconds": 15.0,
            "default_controller_horizon_steps": 1,
            "persistence_fallback_enabled": True,
            "validation_metric_summary": {
                "horizon_steps": 1,
                "model_name": "persistence",
                "macro_mae": 5.0882,
            },
        }

    def test_rejects_production_ready_status(self, frozen_config_dict: dict) -> None:
        bad_conf = dict(frozen_config_dict)
        bad_conf["status"] = "production_ready"
        with pytest.raises(ValueError, match="production_ready.*forbidden"):
            ForecastingService(bad_conf)

    def test_predict_persistence(self, frozen_config_dict: dict) -> None:
        service = ForecastingService(frozen_config_dict)
        inp = _make_valid_input(n_points=5, requested_horizon=1, base_val=42.0)
        out = service.predict(inp)

        assert isinstance(out, ForecastOutput)
        assert out.model_name == "persistence"
        assert out.horizon_steps == 1
        assert out.horizon_seconds == 15.0
        assert out.fallback_used is False
        assert len(out.predicted_values) == 1
        # Last observation in _make_valid_input was 42.0 + 4 = 46.0
        assert np.isclose(out.predicted_values[0], 46.0)
        assert np.isclose(out.predicted_request_rate_per_second, 46.0)

    def test_load_from_yaml_file(self) -> None:
        yaml_path = Path("forecasting/config/final_forecaster.yaml")
        if not yaml_path.exists():
            pytest.skip("final_forecaster.yaml not present in working directory")

        service = ForecastingService.from_config_file(yaml_path)
        assert service.status == "offline_selected"
        assert service.default_horizon_steps == 1

        inp = _make_valid_input(n_points=10, requested_horizon=1)
        out = service.predict(inp)
        assert out.horizon_steps == 1
        assert not out.fallback_used

    def test_persistence_fallback_on_insufficient_history(self) -> None:
        # Configure a model that requires window=8 (e.g., linear_trend)
        conf = {
            "schema_version": "1.0.0",
            "status": "offline_selected",
            "model_family": "linear_trend",
            "model_name": "linear_trend",
            "model_version": "1.0.0",
            "parameters": {"window": 8},
            "sampling_interval_seconds": 15.0,
            "default_controller_horizon_steps": 1,
            "persistence_fallback_enabled": True,
        }
        service = ForecastingService(conf)

        # Provide only 3 observations (< 8 required)
        inp = _make_valid_input(n_points=3, base_val=100.0)
        out = service.predict(inp)

        assert out.fallback_used is True
        assert len(out.warnings) > 0
        assert "Falling back to persistence" in out.warnings[0]
        # Persistence prediction equals last observation (100 + 2 = 102.0)
        assert np.isclose(out.predicted_values[0], 102.0)
        assert np.isclose(out.predicted_request_rate_per_second, 102.0)

    def test_raises_when_fallback_disabled_and_insufficient_history(self) -> None:
        conf = {
            "schema_version": "1.0.0",
            "status": "offline_selected",
            "model_family": "linear_trend",
            "model_name": "linear_trend",
            "parameters": {"window": 8},
            "persistence_fallback_enabled": False,
        }
        service = ForecastingService(conf)
        inp = _make_valid_input(n_points=3)
        with pytest.raises(ValueError, match="insufficient for model.*and fallback not possible"):
            service.predict(inp)

    def test_instantiate_forecaster_unknown_family(self) -> None:
        with pytest.raises(ValueError, match="Unsupported model family"):
            instantiate_forecaster("deep_transformer_v99", {})
