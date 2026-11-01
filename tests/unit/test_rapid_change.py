"""Unit tests for rapid-change detection (Phase 10B)."""

from datetime import UTC, datetime, timedelta

from controller.policy_config import PolicyConfig
from controller.rapid_change import detect_rapid_change
from controller.telemetry import TelemetryResult
from shared.enums import DecisionReason


def _create_config(
    window_samples: int = 3,
    growth_ratio: float = 2.0,
    absolute_rate: float = 5.0,
    min_valid: int = 2,
) -> PolicyConfig:
    return PolicyConfig(
        request_rate_window_samples=window_samples,
        rapid_change_growth_ratio=growth_ratio,
        rapid_change_absolute_rate=absolute_rate,
        minimum_valid_samples=min_valid,
        reactive_cpu_target_ratio=0.70,
        minimum_replicas=1,
        maximum_replicas=10,
        maximum_scale_up_step=3,
        maximum_scale_down_step=1,
        cooldown_seconds=60.0,
        downscale_stabilization_seconds=90.0,
        source="test",
        evidence_reference="rel/path.yaml",
    )


def _create_telemetry(
    values: list[float],
    timestamps: list[datetime] | None = None,
    is_fresh: bool = True,
    status: str = "available_fresh",
) -> TelemetryResult:
    now = datetime.now(UTC)
    if timestamps is None:
        timestamps = [now - timedelta(seconds=(len(values) - i) * 15) for i in range(len(values))]
    return TelemetryResult(
        query_started_at=now - timedelta(seconds=1),
        query_completed_at=now,
        timestamps=timestamps,
        values=values,
        observation_count=len(values),
        latest_age_seconds=1.0 if is_fresh else 120.0,
        is_fresh=is_fresh,
        is_sufficient=len(values) >= 2,
        status=status,  # type: ignore[arg-type]
        expected_interval_seconds=15.0,
    )


class TestRapidChangeDetector:
    def test_stable_series_not_detected(self) -> None:
        cfg = _create_config()
        tel = _create_telemetry([10.0, 10.0, 10.0])
        res = detect_rapid_change(tel, cfg)
        assert res.detected is False
        assert res.reason == DecisionReason.NO_CHANGE_REQUIRED
        assert res.absolute_increase == 0.0
        assert res.growth_ratio == 1.0

    def test_increasing_series_detected(self) -> None:
        cfg = _create_config(window_samples=3, growth_ratio=2.0, absolute_rate=5.0)
        tel = _create_telemetry(
            [2.0, 2.0, 12.0]
        )  # baseline=2.0, delta=10.0 >= 5.0, ratio=6.0 >= 2.0
        res = detect_rapid_change(tel, cfg)
        assert res.detected is True
        assert res.reason == DecisionReason.RAPID_CHANGE_DETECTED
        assert res.recent_rate == 12.0
        assert res.baseline_rate == 2.0
        assert res.absolute_increase == 10.0
        assert res.growth_ratio == 6.0

    def test_exact_threshold_boundary(self) -> None:
        cfg = _create_config(window_samples=3, growth_ratio=2.0, absolute_rate=5.0)
        # baseline=5.0, recent=10.0 -> delta=5.0 (exact), ratio=2.0 (exact)
        tel = _create_telemetry([5.0, 5.0, 10.0])
        res = detect_rapid_change(tel, cfg)
        assert res.detected is True
        assert res.absolute_increase == 5.0
        assert res.growth_ratio == 2.0

    def test_below_ratio_threshold(self) -> None:
        cfg = _create_config(window_samples=3, growth_ratio=2.0, absolute_rate=5.0)
        # baseline=10.0, recent=16.0 -> delta=6.0 >= 5.0, but ratio=1.6 < 2.0
        tel = _create_telemetry([10.0, 10.0, 16.0])
        res = detect_rapid_change(tel, cfg)
        assert res.detected is False
        assert res.reason == DecisionReason.NO_CHANGE_REQUIRED
        assert res.absolute_increase == 6.0
        assert res.growth_ratio == 1.6

    def test_below_absolute_threshold(self) -> None:
        cfg = _create_config(window_samples=3, growth_ratio=2.0, absolute_rate=5.0)
        # baseline=0.5, recent=1.5 -> ratio=3.0 >= 2.0, but delta=1.0 < 5.0
        tel = _create_telemetry([0.5, 0.5, 1.5])
        res = detect_rapid_change(tel, cfg)
        assert res.detected is False
        assert res.reason == DecisionReason.NO_CHANGE_REQUIRED

    def test_near_zero_baseline_safe_division(self) -> None:
        cfg = _create_config(window_samples=3, growth_ratio=2.0, absolute_rate=5.0)
        # baseline = 0.00001 (near zero)
        tel = _create_telemetry([0.00001, 0.00001, 6.0])
        res = detect_rapid_change(tel, cfg)
        assert res.detected is True  # meets both delta >= 5.0 and ratio >= 2.0
        assert res.growth_ratio is not None
        assert res.growth_ratio > 100.0

    def test_zero_baseline(self) -> None:
        cfg = _create_config(window_samples=3, growth_ratio=2.0, absolute_rate=5.0)
        # baseline = 0.0, recent = 0.5 -> delta=0.5 < 5.0 -> NOT detected (noise protected!)
        tel = _create_telemetry([0.0, 0.0, 0.5])
        res = detect_rapid_change(tel, cfg)
        assert res.detected is False

    def test_insufficient_samples(self) -> None:
        cfg = _create_config(window_samples=3, min_valid=2)
        tel = _create_telemetry([10.0])  # Only 1 sample
        res = detect_rapid_change(tel, cfg)
        assert res.detected is False
        assert res.reason == DecisionReason.INSUFFICIENT_HISTORY

    def test_stale_telemetry(self) -> None:
        cfg = _create_config()
        tel = _create_telemetry([2.0, 2.0, 15.0], is_fresh=False)
        res = detect_rapid_change(tel, cfg)
        assert res.detected is False
        assert res.reason == DecisionReason.TELEMETRY_STALE

    def test_unordered_timestamps(self) -> None:
        cfg = _create_config()
        now = datetime.now(UTC)
        timestamps = [now, now - timedelta(seconds=15), now]  # not chronological
        tel = TelemetryResult.model_construct(
            query_started_at=now - timedelta(seconds=1),
            query_completed_at=now,
            timestamps=timestamps,
            values=[2.0, 2.0, 15.0],
            observation_count=3,
            latest_age_seconds=1.0,
            is_fresh=True,
            is_sufficient=True,
            status="available_fresh",
            expected_interval_seconds=15.0,
        )
        res = detect_rapid_change(tel, cfg)
        assert res.detected is False
        assert res.reason == DecisionReason.INSUFFICIENT_HISTORY

    def test_non_finite_values(self) -> None:
        cfg = _create_config()
        now = datetime.now(UTC)
        tel = TelemetryResult.model_construct(
            query_started_at=now - timedelta(seconds=1),
            query_completed_at=now,
            timestamps=[now - timedelta(seconds=30), now - timedelta(seconds=15), now],
            values=[2.0, float("nan"), 15.0],
            observation_count=3,
            latest_age_seconds=1.0,
            is_fresh=True,
            is_sufficient=True,
            status="available_fresh",
            expected_interval_seconds=15.0,
        )
        res = detect_rapid_change(tel, cfg)
        assert res.detected is False
        assert res.reason == DecisionReason.INSUFFICIENT_HISTORY

    def test_no_caller_mutation(self) -> None:
        cfg = _create_config()
        original_vals = [2.0, 2.0, 15.0]
        tel = _create_telemetry(original_vals)
        detect_rapid_change(tel, cfg)
        assert tel.values == [2.0, 2.0, 15.0]
