"""Integration tests for the Phase 11 controller loop with mocked cluster I/O.

These tests use MagicMock for complex data-contract objects (TelemetryResult,
ForecastResult) that the controller merely reads attributes from, to avoid
coupling the test to the full Pydantic constructor requirements.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from controller.capacity_config import CapacityConfig
from controller.config import ObserverConfig
from controller.controller_loop import ControllerLoop
from controller.cpu_telemetry import CpuTelemetryResult
from controller.kubernetes_scale import KubernetesScaleClient, ScaleWriteResult
from controller.policy_config import PolicyConfig
from controller.trust_config import TrustGuardConfig


def _make_telemetry_mock(
    is_available: bool = True,
    values: list[float] | None = None,
    timestamps: list | None = None,
) -> MagicMock:
    """Create a MagicMock mimicking TelemetryResult attribute access."""
    now = datetime.now(UTC)
    vals = values or ([5.0] if is_available else [])
    n = len(vals)
    ts = (
        timestamps
        if timestamps is not None
        else [now - timedelta(seconds=(n - 1 - i) * 10) for i in range(n)]
    )
    mock = MagicMock()
    mock.is_available = is_available
    mock.is_fresh = is_available
    mock.is_sufficient = is_available
    mock.status = "available_fresh" if is_available else "unavailable"
    mock.values = vals
    mock.timestamps = ts
    mock.latest_age_seconds = 3.0
    mock.observation_count = len(vals)
    mock.warnings = []
    return mock


def _make_forecast_mock(predicted_rps: float = 5.0) -> MagicMock:
    """Create a MagicMock mimicking ForecastOutput from forecasting_service.predict."""
    mock = MagicMock()
    mock.model_name = "persistence"
    mock.horizon_seconds = 15.0
    mock.predicted_request_rate_per_second = predicted_rps
    mock.fallback_used = False
    mock.history_count = 5
    mock.warnings = []
    return mock


@pytest.fixture
def test_configs() -> tuple[ObserverConfig, CapacityConfig, TrustGuardConfig, PolicyConfig]:
    obs_cfg = ObserverConfig(
        sampling_interval_seconds=1.0,
        forecast_interval_seconds=1.0,
        history_window_seconds=10.0,
        maximum_cycles=3,
    )
    cap_cfg = CapacityConfig(
        capacity_requests_per_second_per_replica=22.5,
        target_utilization_ratio=0.70,
        safety_margin_ratio=0.90,
        minimum_replicas=1,
        maximum_replicas=10,
        maximum_scale_up_step=3,
        maximum_scale_down_step=1,
        minimum_prediction_rate=0.0,
        capacity_source="unit_test",
        capacity_evidence_reference="tests/integration/test_controller_loop.py",
        workload_profile="test",
    )
    tg_cfg = TrustGuardConfig(
        evidence_window_seconds=180.0,
        minimum_matched_residuals=2,
        residual_max_age_seconds=120.0,
        absolute_error_threshold=10.0,
        relative_error_threshold=50.0,
        minimum_history_observations=1,
        threshold_source="unit_test",
        threshold_evidence_reference="tests/integration/test_controller_loop.py",
    )
    pol_cfg = PolicyConfig(
        request_rate_window_samples=3,
        rapid_change_growth_ratio=2.0,
        rapid_change_absolute_rate=5.0,
        minimum_valid_samples=2,
        reactive_cpu_target_ratio=0.70,
        minimum_replicas=1,
        maximum_replicas=10,
        maximum_scale_up_step=3,
        maximum_scale_down_step=1,
        cooldown_seconds=60.0,
        downscale_stabilization_seconds=90.0,
        source="unit_test",
        evidence_reference="tests/integration/test_controller_loop.py",
    )
    return obs_cfg, cap_cfg, tg_cfg, pol_cfg


class TestControllerLoop:
    """Verify ControllerLoop execution, exception resilience, dry-run, and live scaling paths."""

    def test_dry_run_cycle_execution(
        self,
        tmp_path: Path,
        test_configs: tuple[ObserverConfig, CapacityConfig, TrustGuardConfig, PolicyConfig],
    ) -> None:
        obs_cfg, cap_cfg, tg_cfg, pol_cfg = test_configs
        now = datetime.now(UTC)

        mock_tel = MagicMock()
        mock_tel.read_history.return_value = _make_telemetry_mock(
            is_available=True, values=[5.0, 5.0, 5.0]
        )

        mock_forecaster = MagicMock()
        mock_forecaster.predict.return_value = _make_forecast_mock(predicted_rps=5.0)

        mock_cpu = MagicMock()
        cpu_result = CpuTelemetryResult(
            timestamp=now,
            is_available=True,
            is_fresh=True,
            pod_count=2,
            cpu_utilization_ratio=0.03,
        )
        mock_cpu.get_cpu_telemetry.return_value = cpu_result

        mock_scale = MagicMock(spec=KubernetesScaleClient)
        mock_scale.is_live_enabled = False
        mock_scale.read_workload_scale.return_value = (2, None)
        mock_scale.scale_workload.return_value = ScaleWriteResult(
            attempted_at=now,
            completed_at=now,
            decision_id="cycle-1-test",
            previous_replicas=2,
            requested_replicas=2,
            attempted=False,
            succeeded=False,
            rejected=True,
            rejection_reason="live_scaling_disabled",
        )

        loop = ControllerLoop(
            observer_config=obs_cfg,
            capacity_config=cap_cfg,
            trust_guard_config=tg_cfg,
            policy_config=pol_cfg,
            forecasting_service=mock_forecaster,
            telemetry_reader=mock_tel,
            cpu_telemetry_provider=mock_cpu,
            scale_client=mock_scale,
            output_dir=tmp_path,
            max_cycles=2,
        )

        loop.run()

        assert len(loop._decisions) == 2
        assert len(loop._scale_results) == 2
        assert all(r.attempted is False for r in loop._scale_results)
        assert (tmp_path / "cycles.jsonl").exists()
        assert (tmp_path / "scaling_writes.csv").exists()

    def test_live_scaling_execution(
        self,
        tmp_path: Path,
        test_configs: tuple[ObserverConfig, CapacityConfig, TrustGuardConfig, PolicyConfig],
    ) -> None:
        obs_cfg, cap_cfg, tg_cfg, pol_cfg = test_configs
        now = datetime.now(UTC)

        mock_tel = MagicMock()
        mock_tel.read_history.return_value = _make_telemetry_mock(
            is_available=True, values=[45.0, 45.0, 45.0]
        )

        mock_forecaster = MagicMock()
        mock_forecaster.predict.return_value = _make_forecast_mock(predicted_rps=45.0)

        mock_cpu = MagicMock()
        cpu_result = CpuTelemetryResult(
            timestamp=now,
            is_available=True,
            is_fresh=True,
            pod_count=2,
            cpu_utilization_ratio=0.85,
        )
        mock_cpu.get_cpu_telemetry.return_value = cpu_result

        mock_scale = MagicMock(spec=KubernetesScaleClient)
        mock_scale.is_live_enabled = True
        mock_scale.read_workload_scale.return_value = (2, None)
        mock_scale.scale_workload.return_value = ScaleWriteResult(
            attempted_at=now,
            completed_at=now,
            decision_id="cycle-1-test",
            previous_replicas=2,
            requested_replicas=3,
            observed_replicas_after=3,
            attempted=True,
            succeeded=True,
            rejected=False,
            api_status=200,
            duration_ms=25.0,
        )

        loop = ControllerLoop(
            observer_config=obs_cfg,
            capacity_config=cap_cfg,
            trust_guard_config=tg_cfg,
            policy_config=pol_cfg,
            forecasting_service=mock_forecaster,
            telemetry_reader=mock_tel,
            cpu_telemetry_provider=mock_cpu,
            scale_client=mock_scale,
            output_dir=tmp_path,
            max_cycles=1,
        )

        loop.run()

        assert len(loop._decisions) == 1
        assert len(loop._scale_results) == 1
        assert loop._scale_results[0].succeeded is True
        assert loop._scale_results[0].requested_replicas == 3

    def test_cycle_exception_resilience(
        self,
        tmp_path: Path,
        test_configs: tuple[ObserverConfig, CapacityConfig, TrustGuardConfig, PolicyConfig],
    ) -> None:
        obs_cfg, cap_cfg, tg_cfg, pol_cfg = test_configs
        now = datetime.now(UTC)

        mock_tel = MagicMock()
        # First call raises an exception, second returns valid mock
        mock_tel.read_history.side_effect = [
            RuntimeError("Network blip"),
            _make_telemetry_mock(is_available=True, values=[5.0, 5.0, 5.0]),
        ]

        mock_forecaster = MagicMock()
        mock_forecaster.predict.return_value = None

        mock_cpu = MagicMock()
        mock_cpu.get_cpu_telemetry.return_value = None

        mock_scale = MagicMock(spec=KubernetesScaleClient)
        mock_scale.is_live_enabled = False
        mock_scale.read_workload_scale.return_value = (2, None)
        mock_scale.scale_workload.return_value = ScaleWriteResult(
            attempted_at=now,
            completed_at=now,
            decision_id="test",
            previous_replicas=2,
            requested_replicas=2,
            attempted=False,
            succeeded=False,
            rejected=True,
            rejection_reason="no_recommendation",
        )

        loop = ControllerLoop(
            observer_config=obs_cfg,
            capacity_config=cap_cfg,
            trust_guard_config=tg_cfg,
            policy_config=pol_cfg,
            forecasting_service=mock_forecaster,
            telemetry_reader=mock_tel,
            cpu_telemetry_provider=mock_cpu,
            scale_client=mock_scale,
            output_dir=tmp_path,
            max_cycles=2,
        )

        # Loop must complete both cycles without raising unhandled exception
        loop.run()
        assert len(loop._decisions) == 2
