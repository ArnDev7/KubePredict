"""Unit tests for cooldown manager (Phase 10B)."""

from datetime import UTC, datetime, timedelta

import pytest

from controller.cooldown import CooldownManager
from shared.enums import DecisionReason


class TestCooldownManager:
    def test_no_recent_decision_not_blocked(self) -> None:
        mgr = CooldownManager(cooldown_seconds=60.0)
        now = datetime.now(UTC)
        eval_res = mgr.evaluate(current_time=now, candidate_replicas=4, current_replicas=2)
        assert eval_res.is_active is False
        assert eval_res.is_blocked is False
        assert eval_res.effective_replicas == 4
        assert eval_res.reason_override is None

    def test_inside_cooldown_blocks_scaling_change(self) -> None:
        mgr = CooldownManager(cooldown_seconds=60.0)
        t0 = datetime.now(UTC)
        # Record a scaling action from 2 to 4
        mgr.record_decision(decision_time=t0, final_replicas=4, current_replicas=2)

        # 30 seconds later (inside 60s cooldown), candidate wants 6 replicas (current is now 4)
        t1 = t0 + timedelta(seconds=30)
        eval_res = mgr.evaluate(current_time=t1, candidate_replicas=6, current_replicas=4)
        assert eval_res.is_active is True
        assert eval_res.is_blocked is True
        assert eval_res.effective_replicas == 4  # stays at current replicas
        assert eval_res.reason_override == DecisionReason.COOLDOWN_ACTIVE
        assert eval_res.remaining_seconds == pytest.approx(30.0)

    def test_inside_cooldown_permits_no_change(self) -> None:
        mgr = CooldownManager(cooldown_seconds=60.0)
        t0 = datetime.now(UTC)
        mgr.record_decision(decision_time=t0, final_replicas=4, current_replicas=2)

        # 30 seconds later, candidate recommendation is 4 (same as current replicas 4)
        t1 = t0 + timedelta(seconds=30)
        eval_res = mgr.evaluate(current_time=t1, candidate_replicas=4, current_replicas=4)
        assert eval_res.is_active is True
        assert eval_res.is_blocked is False  # not blocked because no change requested
        assert eval_res.effective_replicas == 4
        assert eval_res.reason_override is None

    def test_exact_cooldown_boundary(self) -> None:
        mgr = CooldownManager(cooldown_seconds=60.0)
        t0 = datetime.now(UTC)
        mgr.record_decision(decision_time=t0, final_replicas=4, current_replicas=2)

        # Exactly 60.0 seconds later
        t_boundary = t0 + timedelta(seconds=60.0)
        eval_res = mgr.evaluate(current_time=t_boundary, candidate_replicas=5, current_replicas=4)
        assert eval_res.is_active is False
        assert eval_res.is_blocked is False
        assert eval_res.effective_replicas == 5

    def test_outside_cooldown(self) -> None:
        mgr = CooldownManager(cooldown_seconds=60.0)
        t0 = datetime.now(UTC)
        mgr.record_decision(decision_time=t0, final_replicas=4, current_replicas=2)

        # 61 seconds later
        t1 = t0 + timedelta(seconds=61.0)
        eval_res = mgr.evaluate(current_time=t1, candidate_replicas=2, current_replicas=4)
        assert eval_res.is_active is False
        assert eval_res.is_blocked is False
        assert eval_res.effective_replicas == 2

    def test_negative_cooldown_rejected(self) -> None:
        with pytest.raises(ValueError):
            CooldownManager(cooldown_seconds=-10.0)

    def test_time_travel_backwards_rejected(self) -> None:
        mgr = CooldownManager(cooldown_seconds=60.0)
        t0 = datetime.now(UTC)
        mgr.record_decision(decision_time=t0, final_replicas=4, current_replicas=2)

        with pytest.raises(ValueError, match="earlier than last scaling time"):
            mgr.evaluate(
                current_time=t0 - timedelta(seconds=10), candidate_replicas=5, current_replicas=4
            )
