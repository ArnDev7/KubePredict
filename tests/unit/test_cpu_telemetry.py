"""Unit tests for read-only CPU telemetry reader (Phase 10B)."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from controller.cpu_telemetry import (
    ReadOnlyCpuTelemetryReader,
    parse_cpu_quantity,
)


class TestCpuTelemetry:
    def test_parse_cpu_quantities(self) -> None:
        assert parse_cpu_quantity("2m") == 0.002
        assert parse_cpu_quantity("500u") == 0.0005
        assert parse_cpu_quantity("2000000n") == 0.002
        assert parse_cpu_quantity("1") == 1.0
        assert parse_cpu_quantity("0.5") == 0.5

    def test_parse_empty_quantity_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_cpu_quantity("")

    @patch("subprocess.run")
    def test_successful_cpu_reading(self, mock_run: MagicMock) -> None:
        # Mock 2 pods: each using 2m CPU. Total request: 2 * 0.1 = 0.2 cores.
        # Total usage: 4m = 0.004 cores. Ratio: 0.004 / 0.200 = 0.02 (2%)
        mock_output = (
            "kubepredict-workload-api-1   2m   46Mi\nkubepredict-workload-api-2   2m   46Mi\n"
        )
        mock_run.return_value = subprocess.CompletedProcess(
            args=["kubectl", "top", "pod"],
            returncode=0,
            stdout=mock_output,
            stderr="",
        )

        reader = ReadOnlyCpuTelemetryReader()
        res = reader.get_cpu_telemetry()

        assert res.is_available is True
        assert res.is_fresh is True
        assert res.pod_count == 2
        assert res.total_cpu_cores == pytest.approx(0.004)
        assert res.requested_cpu_cores == pytest.approx(0.200)
        assert res.cpu_utilization_ratio == pytest.approx(0.02)
        assert len(res.pod_metrics) == 2

    @patch("subprocess.run")
    def test_timeout_handling(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["kubectl"], timeout=8.0)
        reader = ReadOnlyCpuTelemetryReader()
        res = reader.get_cpu_telemetry()

        assert res.is_available is False
        assert res.is_fresh is False
        assert res.pod_count == 0
        assert "timed out" in (res.error_message or "")

    @patch("subprocess.run")
    def test_nonzero_exit_code(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["kubectl", "top", "pod"],
            returncode=1,
            stdout="",
            stderr="Error from server (NotFound): pods not found",
        )
        reader = ReadOnlyCpuTelemetryReader()
        res = reader.get_cpu_telemetry()

        assert res.is_available is False
        assert res.is_fresh is False
        assert "failed" in (res.error_message or "")

    @patch("subprocess.run")
    def test_empty_pod_output(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["kubectl", "top", "pod"],
            returncode=0,
            stdout="\n",
            stderr="",
        )
        reader = ReadOnlyCpuTelemetryReader()
        res = reader.get_cpu_telemetry()

        assert res.is_available is False
        assert res.pod_count == 0
