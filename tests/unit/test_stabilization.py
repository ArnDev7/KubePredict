"""Unit tests for downscale stabilization manager (Phase 10B)."""

from datetime import UTC, datetime, timedelta

import pytest

from controller.stabilization import DownscaleStabilizer
from shared.enums import DecisionReason


class TestDownscaleStabilizer:
    def test_scale_up_unaffected(self) -> None:
        stab = DownscaleStabilizer(stabilization_window_seconds=90.0)
        t0 = datetime.now(UTC)
        stab.record_recommendation(t0 - timedelta(seconds=20), 2)

        # Scale up from 2 to 5 -> stabilization does not damp scale-up
        res = stab.evaluate(current_time=t0, candidate_replicas=5, current_replicas=2)
        assert res.is_stabilized is False
        assert res.stabilized_replicas == 5
        assert res.reason_override is None

    def test_downscale_within_window_selects_safest_higher(self) -> None:
        stab = DownscaleStabilizer(stabilization_window_seconds=90.0)
        t0 = datetime.now(UTC)
        # 30s ago, recommendation was 5
        stab.record_recommendation(t0 - timedelta(seconds=30), 5)

        # Now current_replicas is 5, candidate wants to drop to 2
        # Safest higher recommendation in window is 5
        res = stab.evaluate(current_time=t0, candidate_replicas=2, current_replicas=5)
        assert res.is_stabilized is True
        assert res.stabilized_replicas == 5  # damped to 5
        assert res.reason_override == DecisionReason.DOWNSCALE_STABILIZED
        assert res.window_max_replicas == 5

    def test_multiple_recent_recommendations_in_window(self) -> None:
        stab = DownscaleStabilizer(stabilization_window_seconds=90.0)
        t0 = datetime.now(UTC)
        # History: 60s ago was 3, 30s ago was 4
        stab.record_recommendation(t0 - timedelta(seconds=60), 3)
        stab.record_recommendation(t0 - timedelta(seconds=30), 4)

        # Current is 4, candidate wants 1
        res = stab.evaluate(current_time=t0, candidate_replicas=1, current_replicas=4)
        assert res.is_stabilized is True
        assert res.stabilized_replicas == 4  # max in window is 4

    def test_expired_history_pruned(self) -> None:
        stab = DownscaleStabilizer(stabilization_window_seconds=60.0)
        t0 = datetime.now(UTC)
        # 120s ago was 5 (outside 60s window)
        stab.record_recommendation(t0 - timedelta(seconds=120), 5)

        # Candidate wants 2 (current is 5)
        res = stab.evaluate(current_time=t0, candidate_replicas=2, current_replicas=5)
        # 120s ago is pruned, history is effectively empty
        assert res.is_stabilized is False
        assert res.stabilized_replicas == 2

    def test_duplicate_timestamp_conflicting_rejected(self) -> None:
        stab = DownscaleStabilizer(stabilization_window_seconds=60.0)
        t0 = datetime.now(UTC)
        stab.record_recommendation(t0, 3)
        with pytest.raises(ValueError, match="Duplicate timestamp"):
            stab.record_recommendation(t0, 5)

    def test_duplicate_timestamp_same_value_idempotent(self) -> None:
        stab = DownscaleStabilizer(stabilization_window_seconds=60.0)
        t0 = datetime.now(UTC)
        stab.record_recommendation(t0, 3)
        stab.record_recommendation(t0, 3)  # same recommendation, should not raise
        assert len(stab._history) == 1
