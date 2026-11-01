"""Unit tests for Forecast Trust Guard evaluation logic (Phase 10A)."""

from datetime import UTC, datetime, timedelta

import pytest

from controller.forecast_store import PendingForecast
from controller.telemetry import TelemetryResult
from controller.trust_config import TrustGuardConfig
from controller.trust_guard import evaluate_forecast_trust
from shared.enums import PrimaryErrorMetric, TrustOutcome


def get_default_trust_config() -> TrustGuardConfig:
    return TrustGuardConfig(
        schema_version="1.0.0",
        status="provisional_validated",
        enabled=True,
        evidence_window_seconds=180.0,
        minimum_matched_residuals=3,
        residual_max_age_seconds=120.0,
        absolute_error_threshold=10.0,
        relative_error_threshold=50.0,
        primary_error_metric=PrimaryErrorMetric.MAE,
        require_fresh_telemetry=True,
        require_valid_forecast=True,
        minimum_history_observations=1,
        threshold_source="offline_scenario_validation_and_live_baseline_residuals",
        threshold_evidence_reference="forecasting/config/final_forecaster.yaml#L70-L79;experiments/output/controller-dry-run/dry-run-20261027_105511/matched_residuals.csv",
    )


def make_fresh_telemetry(
    now: datetime, obs_count: int = 5, status: str = "available_fresh", is_fresh: bool = True
) -> TelemetryResult:
    timestamps = [now - timedelta(seconds=15 * (obs_count - i)) for i in range(obs_count)]
    values = [10.0] * obs_count
    return TelemetryResult(
        query_started_at=now - timedelta(seconds=2),
        query_completed_at=now,
        expected_interval_seconds=15.0,
        timestamps=timestamps,
        values=values,
        observation_count=obs_count,
        latest_age_seconds=15.0,
        is_fresh=is_fresh,
        is_sufficient=(obs_count >= 1),
        status=status,  # type: ignore[arg-type]
    )


def make_forecast(now: datetime, pred_rate: float = 10.0) -> PendingForecast:
    return PendingForecast(
        forecast_id="fc-test-01",
        generated_at=now - timedelta(seconds=15),
        target_timestamp=now,
        horizon_seconds=15.0,
        model_name="persistence",
        predicted_request_rate_per_second=pred_rate,
        history_count=1,
    )


def make_residuals(
    now: datetime,
    count: int = 3,
    error: float = 0.5,
    age_offset: float = 0.0,
) -> list[PendingForecast]:
    records: list[PendingForecast] = []
    for i in range(1, count + 1):
        target_ts = now - timedelta(seconds=(age_offset + 15 * i))
        pred = 10.0
        actual = pred + error
        records.append(
            PendingForecast(
                forecast_id=f"res-{i}",
                generated_at=target_ts - timedelta(seconds=15),
                target_timestamp=target_ts,
                horizon_seconds=15.0,
                model_name="persistence",
                predicted_request_rate_per_second=pred,
                actual_request_rate_per_second=actual,
                residual=round(actual - pred, 4),
                absolute_error=round(abs(actual - pred), 4),
                status="matched",
                history_count=1,
            )
        )
    return records


class TestTrustGuardOutcomes:
    """Validate all deterministic trust guard outcomes and policy short-circuits."""

    def test_fresh_valid_trusted_forecast(self) -> None:
        now = datetime.now(UTC)
        cfg = get_default_trust_config()
        telem = make_fresh_telemetry(now)
        fc = make_forecast(now, 10.0)
        res = make_residuals(now, count=3, error=0.2)

        decision = evaluate_forecast_trust(now, cfg, telem, fc, res)

        assert decision.trusted is True
        assert decision.outcome == TrustOutcome.TRUSTED
        assert decision.recent_mae == 0.2
        assert decision.forecast_id == "fc-test-01"
        assert decision.history_observations == 5
        assert decision.matched_residual_count == 3

    def test_guard_disabled(self) -> None:
        now = datetime.now(UTC)
        cfg = get_default_trust_config()
        cfg.enabled = False
        telem = make_fresh_telemetry(now)
        fc = make_forecast(now)
        res = make_residuals(now, count=3)

        decision = evaluate_forecast_trust(now, cfg, telem, fc, res)

        assert decision.trusted is False
        assert decision.outcome == TrustOutcome.GUARD_DISABLED

    def test_telemetry_unavailable(self) -> None:
        now = datetime.now(UTC)
        cfg = get_default_trust_config()
        fc = make_forecast(now)
        res = make_residuals(now, count=3)

        # None telemetry
        d1 = evaluate_forecast_trust(now, cfg, None, fc, res)
        assert d1.trusted is False
        assert d1.outcome == TrustOutcome.TELEMETRY_UNAVAILABLE

        # Status unavailable
        telem_unavail = make_fresh_telemetry(now, status="unavailable", is_fresh=False)
        d2 = evaluate_forecast_trust(now, cfg, telem_unavail, fc, res)
        assert d2.trusted is False
        assert d2.outcome == TrustOutcome.TELEMETRY_UNAVAILABLE

    def test_telemetry_stale(self) -> None:
        now = datetime.now(UTC)
        cfg = get_default_trust_config()
        telem_stale = make_fresh_telemetry(now, status="stale", is_fresh=False)
        fc = make_forecast(now)
        res = make_residuals(now, count=3)

        decision = evaluate_forecast_trust(now, cfg, telem_stale, fc, res)

        assert decision.trusted is False
        assert decision.outcome == TrustOutcome.TELEMETRY_STALE

    def test_insufficient_history(self) -> None:
        now = datetime.now(UTC)
        cfg = get_default_trust_config()
        cfg.minimum_history_observations = 5
        telem_short = make_fresh_telemetry(now, obs_count=2)
        fc = make_forecast(now)
        res = make_residuals(now, count=3)

        decision = evaluate_forecast_trust(now, cfg, telem_short, fc, res)

        assert decision.trusted is False
        assert decision.outcome == TrustOutcome.INSUFFICIENT_HISTORY

    def test_forecast_unavailable(self) -> None:
        now = datetime.now(UTC)
        cfg = get_default_trust_config()
        telem = make_fresh_telemetry(now)
        res = make_residuals(now, count=3)

        decision = evaluate_forecast_trust(now, cfg, telem, None, res)

        assert decision.trusted is False
        assert decision.outcome == TrustOutcome.FORECAST_UNAVAILABLE

    def test_invalid_forecast(self) -> None:
        now = datetime.now(UTC)
        cfg = get_default_trust_config()
        telem = make_fresh_telemetry(now)
        fc_neg = make_forecast(now, pred_rate=10.0)
        object.__setattr__(fc_neg, "predicted_request_rate_per_second", -5.0)
        res = make_residuals(now, count=3)

        decision = evaluate_forecast_trust(now, cfg, telem, fc_neg, res)

        assert decision.trusted is False
        assert decision.outcome == TrustOutcome.FORECAST_INVALID

    def test_insufficient_residuals(self) -> None:
        now = datetime.now(UTC)
        cfg = get_default_trust_config()
        telem = make_fresh_telemetry(now)
        fc = make_forecast(now)
        # only 2 residuals when 3 required
        res_few = make_residuals(now, count=2)

        decision = evaluate_forecast_trust(now, cfg, telem, fc, res_few)

        assert decision.trusted is False
        assert decision.outcome == TrustOutcome.INSUFFICIENT_RESIDUAL_HISTORY
        assert decision.recent_mae is None  # Metrics absent when evidence insufficient!

    def test_stale_residual_history(self) -> None:
        now = datetime.now(UTC)
        cfg = get_default_trust_config()
        telem = make_fresh_telemetry(now)
        fc = make_forecast(now)
        # Residuals within evidence window (180s) but latest residual older than residual_max_age_seconds (120s)
        # Ages: 130s, 140s, 150s (all < 180s, latest is 130s > 120s)
        res_old = [
            PendingForecast(
                forecast_id=f"res-stale-{i}",
                generated_at=now - timedelta(seconds=130 + 10 * i + 15),
                target_timestamp=now - timedelta(seconds=130 + 10 * i),
                horizon_seconds=15.0,
                model_name="persistence",
                predicted_request_rate_per_second=10.0,
                actual_request_rate_per_second=10.1,
                residual=0.1,
                absolute_error=0.1,
                status="matched",
                history_count=1,
            )
            for i in range(3)
        ]

        decision = evaluate_forecast_trust(now, cfg, telem, fc, res_old)

        assert decision.trusted is False
        assert decision.outcome == TrustOutcome.RESIDUAL_HISTORY_STALE

    def test_absolute_error_too_high(self) -> None:
        now = datetime.now(UTC)
        cfg = get_default_trust_config()
        cfg.absolute_error_threshold = 5.0
        cfg.relative_error_threshold = 100.0  # high relative threshold
        telem = make_fresh_telemetry(now)
        fc = make_forecast(now)
        # error 8.0 > 5.0 absolute threshold
        res = make_residuals(now, count=3, error=8.0)

        decision = evaluate_forecast_trust(now, cfg, telem, fc, res)

        assert decision.trusted is False
        assert decision.outcome == TrustOutcome.ABSOLUTE_ERROR_TOO_HIGH
        assert decision.recent_mae == 8.0

    def test_relative_error_too_high(self) -> None:
        now = datetime.now(UTC)
        cfg = get_default_trust_config()
        cfg.absolute_error_threshold = 50.0  # high absolute threshold
        cfg.relative_error_threshold = 20.0  # low relative threshold
        telem = make_fresh_telemetry(now)
        fc = make_forecast(now)
        # actual = 15, pred = 10 -> diff = 5 <= 50.0 MAE, but sMAPE = 2*5/25 * 100% = 40.0% > 20%
        res = make_residuals(now, count=3, error=5.0)

        decision = evaluate_forecast_trust(now, cfg, telem, fc, res)

        assert decision.trusted is False
        assert decision.outcome == TrustOutcome.RELATIVE_ERROR_TOO_HIGH
        assert decision.recent_smape == pytest.approx(40.0, rel=1e-3)

    def test_both_thresholds_exceeded(self) -> None:
        now = datetime.now(UTC)
        cfg = get_default_trust_config()
        cfg.absolute_error_threshold = 5.0
        cfg.relative_error_threshold = 20.0
        telem = make_fresh_telemetry(now)
        fc = make_forecast(now)
        # actual = 20, pred = 10 -> MAE = 10 > 5.0, sMAPE = 2*10/30 = 66.67% > 20%
        res = make_residuals(now, count=3, error=10.0)

        decision = evaluate_forecast_trust(now, cfg, telem, fc, res)

        assert decision.trusted is False
        assert decision.outcome == TrustOutcome.ERROR_THRESHOLD_EXCEEDED

    def test_exact_threshold_boundary_is_trusted(self) -> None:
        now = datetime.now(UTC)
        cfg = get_default_trust_config()
        cfg.absolute_error_threshold = 5.0
        telem = make_fresh_telemetry(now)
        fc = make_forecast(now)
        # error exactly 5.0
        res = make_residuals(now, count=3, error=5.0)
        cfg.relative_error_threshold = 50.0  # sMAPE for 10 vs 15 is 40.0% <= 50.0%

        decision = evaluate_forecast_trust(now, cfg, telem, fc, res)

        assert decision.trusted is True
        assert decision.outcome == TrustOutcome.TRUSTED

    def test_zero_traffic_trusted_when_criteria_pass(self) -> None:
        now = datetime.now(UTC)
        cfg = get_default_trust_config()
        telem_zero = make_fresh_telemetry(now)
        fc_zero = make_forecast(now, pred_rate=0.0)
        res_zero = [
            PendingForecast(
                forecast_id=f"z-{i}",
                generated_at=now - timedelta(seconds=15 * (i + 1)),
                target_timestamp=now - timedelta(seconds=15 * i),
                horizon_seconds=15.0,
                model_name="persistence",
                predicted_request_rate_per_second=0.0,
                actual_request_rate_per_second=0.0,
                residual=0.0,
                absolute_error=0.0,
                status="matched",
                history_count=1,
            )
            for i in range(1, 4)
        ]

        decision = evaluate_forecast_trust(now, cfg, telem_zero, fc_zero, res_zero)

        assert decision.trusted is True
        assert decision.outcome == TrustOutcome.TRUSTED
        assert decision.recent_mae == 0.0
        assert decision.recent_smape == 0.0

    def test_no_probability_output_in_decision(self) -> None:
        now = datetime.now(UTC)
        cfg = get_default_trust_config()
        telem = make_fresh_telemetry(now)
        fc = make_forecast(now)
        res = make_residuals(now, count=3)

        decision = evaluate_forecast_trust(now, cfg, telem, fc, res)

        dump = decision.model_dump()
        assert "confidence" not in dump
        assert "probability" not in dump
        assert "confidence_score" not in dump
        assert "desired_replicas" not in dump
        assert "final_replicas" not in dump
