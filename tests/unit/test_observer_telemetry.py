"""Unit tests for LiveTelemetryReader and TelemetryResult (Phase 9A)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from controller.config import ObserverConfig
from controller.telemetry import LiveTelemetryReader, TelemetryResult
from experiments.telemetry.prometheus_client import (
    MetricSample,
    MetricSeries,
    PrometheusClient,
    RangeQueryResult,
)


def _make_sample_series(
    timestamps: list[datetime],
    values: list[float],
) -> RangeQueryResult:
    samples = [MetricSample(timestamp=ts, value=val) for ts, val in zip(timestamps, values, strict=True)]
    series = MetricSeries(labels={"job": "kubepredict-workload"}, samples=samples)
    return RangeQueryResult(
        field_name="request_rate_per_second",
        query="sum(rate(...))",
        result_type="matrix",
        series=[series],
    )


class TestTelemetryResultContract:
    """Validate TelemetryResult contract schema invariants."""

    def test_valid_telemetry_result(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        res = TelemetryResult(
            query_started_at=now - timedelta(seconds=1),
            query_completed_at=now,
            observation_start=now - timedelta(seconds=30),
            observation_end=now,
            expected_interval_seconds=15.0,
            timestamps=[now - timedelta(seconds=15), now],
            values=[10.0, 20.0],
            observation_count=2,
            missing_interval_count=0,
            latest_age_seconds=0.0,
            is_fresh=True,
            is_sufficient=True,
            status="available_fresh",
        )
        assert res.observation_count == 2
        assert res.status == "available_fresh"

    def test_rejects_mismatched_lengths(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValidationError, match="Length of timestamps"):
            TelemetryResult(
                query_started_at=now,
                query_completed_at=now,
                expected_interval_seconds=15.0,
                timestamps=[now],
                values=[10.0, 20.0],
                observation_count=2,
                latest_age_seconds=0.0,
                status="available_fresh",
            )

    def test_rejects_mismatched_observation_count(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValidationError, match="observation_count"):
            TelemetryResult(
                query_started_at=now,
                query_completed_at=now,
                expected_interval_seconds=15.0,
                timestamps=[now],
                values=[10.0],
                observation_count=5,  # Mismatched
                latest_age_seconds=0.0,
                status="available_fresh",
            )

    def test_rejects_negative_value(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValidationError, match="non-negative"):
            TelemetryResult(
                query_started_at=now,
                query_completed_at=now,
                expected_interval_seconds=15.0,
                timestamps=[now],
                values=[-5.0],
                observation_count=1,
                latest_age_seconds=0.0,
                status="available_fresh",
            )


class TestLiveTelemetryReader:
    """Validate LiveTelemetryReader parsing, validation, and error classification."""

    @pytest.fixture
    def config(self) -> ObserverConfig:
        return ObserverConfig(
            sampling_interval_seconds=15.0,
            history_window_seconds=180.0,
            telemetry_stale_after_seconds=30.0,
        )

    def test_fresh_sufficient_telemetry(self, config: ObserverConfig) -> None:
        mock_client = MagicMock(spec=PrometheusClient)
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        ts_list = [now - timedelta(seconds=(5 - i) * 15) for i in range(6)]
        val_list = [10.0, 12.0, 15.0, 18.0, 20.0, 25.0]

        mock_client.query_range.return_value = _make_sample_series(ts_list, val_list)

        reader = LiveTelemetryReader(config, client=mock_client)
        result = reader.read_history(current_time=now, minimum_history=3)

        assert result.status == "available_fresh"
        assert result.is_fresh is True
        assert result.is_sufficient is True
        assert result.observation_count == 6
        assert result.values == val_list
        assert result.missing_interval_count == 0

    def test_stale_telemetry_detected(self, config: ObserverConfig) -> None:
        mock_client = MagicMock(spec=PrometheusClient)
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        # Latest sample is 60s old (> 30s stale threshold)
        ts_list = [now - timedelta(seconds=90), now - timedelta(seconds=60)]
        val_list = [10.0, 12.0]

        mock_client.query_range.return_value = _make_sample_series(ts_list, val_list)

        reader = LiveTelemetryReader(config, client=mock_client)
        result = reader.read_history(current_time=now, minimum_history=1)

        assert result.status == "stale"
        assert result.is_fresh is False
        assert result.latest_age_seconds >= 60.0

    def test_insufficient_history_detected(self, config: ObserverConfig) -> None:
        mock_client = MagicMock(spec=PrometheusClient)
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        ts_list = [now]
        val_list = [15.0]

        mock_client.query_range.return_value = _make_sample_series(ts_list, val_list)

        reader = LiveTelemetryReader(config, client=mock_client)
        result = reader.read_history(current_time=now, minimum_history=8)

        assert result.status == "available_insufficient"
        assert result.is_sufficient is False
        assert result.observation_count == 1

    def test_empty_results_unavailable(self, config: ObserverConfig) -> None:
        mock_client = MagicMock(spec=PrometheusClient)
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)

        mock_client.query_range.return_value = RangeQueryResult(
            field_name="request_rate_per_second",
            query="sum(...)",
            result_type="matrix",
            series=[],
        )

        reader = LiveTelemetryReader(config, client=mock_client)
        result = reader.read_history(current_time=now)

        assert result.status == "unavailable"
        assert result.observation_count == 0
        assert result.is_fresh is False

    def test_client_exception_returns_unavailable(self, config: ObserverConfig) -> None:
        mock_client = MagicMock(spec=PrometheusClient)
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        mock_client.query_range.side_effect = RuntimeError("Connection refused")

        reader = LiveTelemetryReader(config, client=mock_client)
        result = reader.read_history(current_time=now)

        assert result.status == "unavailable"
        assert "Connection refused" in result.warnings[0]

    def test_missing_intervals_counted(self, config: ObserverConfig) -> None:
        mock_client = MagicMock(spec=PrometheusClient)
        now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        # Gap of 45s between samples (3 intervals instead of 1 -> 2 missed)
        ts_list = [now - timedelta(seconds=60), now - timedelta(seconds=15), now]
        val_list = [10.0, 20.0, 30.0]

        mock_client.query_range.return_value = _make_sample_series(ts_list, val_list)

        reader = LiveTelemetryReader(config, client=mock_client)
        result = reader.read_history(current_time=now, minimum_history=1)

        assert result.missing_interval_count == 2
