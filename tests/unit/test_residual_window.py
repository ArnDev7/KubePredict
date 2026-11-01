"""Unit tests for residual_window.py (Phase 10A)."""

from datetime import UTC, datetime, timedelta

import pytest

from controller.forecast_store import PendingForecast
from controller.residual_window import build_residual_window


def make_pending_forecast(
    forecast_id: str,
    gen_time: datetime,
    target_time: datetime,
    status: str = "matched",
    predicted: float = 10.0,
    actual: float | None = 10.0,
    residual: float | None = 0.0,
    abs_err: float | None = 0.0,
) -> PendingForecast:
    return PendingForecast(
        forecast_id=forecast_id,
        generated_at=gen_time,
        target_timestamp=target_time,
        horizon_seconds=(target_time - gen_time).total_seconds(),
        model_name="persistence",
        predicted_request_rate_per_second=predicted,
        actual_request_rate_per_second=actual,
        residual=residual,
        absolute_error=abs_err,
        status=status,  # type: ignore[arg-type]
        history_count=1,
    )


class TestResidualWindow:
    """Validate residual window construction rules and edge cases."""

    def test_valid_matched_records_included(self) -> None:
        now = datetime.now(UTC)
        records = [
            make_pending_forecast("f1", now - timedelta(seconds=60), now - timedelta(seconds=45)),
            make_pending_forecast("f2", now - timedelta(seconds=45), now - timedelta(seconds=30)),
            make_pending_forecast("f3", now - timedelta(seconds=30), now - timedelta(seconds=15)),
        ]

        res = build_residual_window(
            matched_records=records,
            evaluation_time=now,
            evidence_window_seconds=180.0,
            residual_max_age_seconds=60.0,
            minimum_matched_residuals=3,
        )

        assert len(res.eligible_records) == 3
        assert res.excluded_too_old_count == 0
        assert res.excluded_future_count == 0
        assert res.excluded_invalid_count == 0
        assert res.evidence_sufficiency is True
        assert res.latest_residual_age_seconds == pytest.approx(15.0, abs=0.1)

    def test_unordered_input_sorted_deterministically(self) -> None:
        now = datetime.now(UTC)
        r1 = make_pending_forecast("f1", now - timedelta(seconds=60), now - timedelta(seconds=45))
        r2 = make_pending_forecast("f2", now - timedelta(seconds=45), now - timedelta(seconds=30))
        r3 = make_pending_forecast("f3", now - timedelta(seconds=30), now - timedelta(seconds=15))

        # Pass in reverse order
        res = build_residual_window(
            matched_records=[r3, r1, r2],
            evaluation_time=now,
            evidence_window_seconds=180.0,
            residual_max_age_seconds=60.0,
        )

        assert [r.forecast_id for r in res.eligible_records] == ["f1", "f2", "f3"]

    def test_too_old_records_excluded(self) -> None:
        now = datetime.now(UTC)
        # Window is 60s. r1 is 90s old -> excluded
        r1 = make_pending_forecast("f1", now - timedelta(seconds=105), now - timedelta(seconds=90))
        r2 = make_pending_forecast("f2", now - timedelta(seconds=45), now - timedelta(seconds=30))

        res = build_residual_window(
            matched_records=[r1, r2],
            evaluation_time=now,
            evidence_window_seconds=60.0,
            residual_max_age_seconds=60.0,
        )

        assert len(res.eligible_records) == 1
        assert res.eligible_records[0].forecast_id == "f2"
        assert res.excluded_too_old_count == 1

    def test_future_records_excluded(self) -> None:
        now = datetime.now(UTC)
        # Target in the future
        r_fut = make_pending_forecast("f_fut", now, now + timedelta(seconds=15))
        r_past = make_pending_forecast(
            "f_past", now - timedelta(seconds=30), now - timedelta(seconds=15)
        )

        res = build_residual_window(
            matched_records=[r_fut, r_past],
            evaluation_time=now,
            evidence_window_seconds=180.0,
            residual_max_age_seconds=60.0,
        )

        assert len(res.eligible_records) == 1
        assert res.eligible_records[0].forecast_id == "f_past"
        assert res.excluded_future_count == 1

    def test_invalid_and_unmatched_records_excluded(self) -> None:
        now = datetime.now(UTC)
        r_pend = make_pending_forecast(
            "f_pend",
            now - timedelta(seconds=30),
            now - timedelta(seconds=15),
            status="pending",
            actual=None,
        )
        r_mat = make_pending_forecast(
            "f_mat", now - timedelta(seconds=30), now - timedelta(seconds=15)
        )

        res = build_residual_window(
            matched_records=[r_pend, r_mat],
            evaluation_time=now,
            evidence_window_seconds=180.0,
            residual_max_age_seconds=60.0,
        )

        assert len(res.eligible_records) == 1
        assert res.eligible_records[0].forecast_id == "f_mat"
        assert res.excluded_invalid_count == 1

    def test_empty_window_supported(self) -> None:
        now = datetime.now(UTC)
        res = build_residual_window(
            matched_records=[],
            evaluation_time=now,
            evidence_window_seconds=180.0,
            residual_max_age_seconds=60.0,
            minimum_matched_residuals=3,
        )

        assert len(res.eligible_records) == 0
        assert res.latest_residual_timestamp is None
        assert res.latest_residual_age_seconds is None
        assert res.evidence_sufficiency is False

    def test_duplicate_forecast_ids_rejected(self) -> None:
        now = datetime.now(UTC)
        r1 = make_pending_forecast(
            "dup-id", now - timedelta(seconds=30), now - timedelta(seconds=15)
        )
        r2 = make_pending_forecast(
            "dup-id", now - timedelta(seconds=25), now - timedelta(seconds=10)
        )

        with pytest.raises(ValueError, match="Duplicate forecast ID"):
            build_residual_window(
                matched_records=[r1, r2],
                evaluation_time=now,
                evidence_window_seconds=180.0,
                residual_max_age_seconds=60.0,
            )

    def test_exact_boundary_inclusion(self) -> None:
        now = datetime.now(UTC)
        # Exactly on the lower boundary: now - 60s
        target_exact = now - timedelta(seconds=60.0)
        r_exact = make_pending_forecast(
            "f_boundary", target_exact - timedelta(seconds=15), target_exact
        )

        res = build_residual_window(
            matched_records=[r_exact],
            evaluation_time=now,
            evidence_window_seconds=60.0,
            residual_max_age_seconds=60.0,
        )

        assert len(res.eligible_records) == 1
        assert res.eligible_records[0].forecast_id == "f_boundary"

    def test_no_source_mutation(self) -> None:
        now = datetime.now(UTC)
        records = [
            make_pending_forecast("f1", now - timedelta(seconds=60), now - timedelta(seconds=45)),
            make_pending_forecast("f2", now - timedelta(seconds=45), now - timedelta(seconds=30)),
        ]
        orig_len = len(records)
        orig_first_id = records[0].forecast_id

        build_residual_window(
            matched_records=records,
            evaluation_time=now,
            evidence_window_seconds=180.0,
            residual_max_age_seconds=60.0,
        )

        assert len(records) == orig_len
        assert records[0].forecast_id == orig_first_id
