"""Cooldown tracking and evaluation for KubePredict controller (Phase 10B).

Enforces stabilization cooldown after scaling recommendation changes to prevent
rapid oscillation. Operates strictly in-memory in dry-run mode with zero wall-clock
dependencies, supporting full deterministic time injection.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from shared.enums import DecisionReason
from shared.validation import require_timezone_aware


@dataclass(frozen=True)
class CooldownEvaluation:
    """Result of evaluating cooldown against a candidate scaling recommendation."""

    is_active: bool
    is_blocked: bool
    remaining_seconds: float
    last_scaling_time: datetime | None
    effective_replicas: int
    reason_override: DecisionReason | None
    details: dict[str, Any]


class CooldownManager:
    """In-memory manager tracking scaling decision timestamps for cooldown dampening."""

    def __init__(self, cooldown_seconds: float) -> None:
        if cooldown_seconds < 0.0:
            raise ValueError(f"cooldown_seconds must be >= 0.0, got {cooldown_seconds}")
        self.cooldown_seconds = float(cooldown_seconds)
        self.last_scaling_time: datetime | None = None
        self.last_scaling_replicas: int | None = None

    def evaluate(
        self,
        current_time: datetime,
        candidate_replicas: int,
        current_replicas: int,
    ) -> CooldownEvaluation:
        """Evaluate if candidate recommendation is permitted or blocked by cooldown.

        Parameters
        ----------
        current_time : datetime
            Time of evaluation (must be timezone-aware).
        candidate_replicas : int
            Recommended replica count before cooldown dampening.
        current_replicas : int
            Current actual replica count.

        Returns
        -------
        CooldownEvaluation
            Outcome indicating whether cooldown is active/blocked and effective replicas.
        """
        curr_ts = require_timezone_aware(current_time)

        if self.last_scaling_time is None or self.cooldown_seconds <= 0.0:
            return CooldownEvaluation(
                is_active=False,
                is_blocked=False,
                remaining_seconds=0.0,
                last_scaling_time=self.last_scaling_time,
                effective_replicas=candidate_replicas,
                reason_override=None,
                details={"cooldown_seconds": self.cooldown_seconds},
            )

        elapsed = (curr_ts - self.last_scaling_time).total_seconds()
        if elapsed < 0.0:
            # Current time is earlier than last scaling time - reject or ignore
            raise ValueError(
                f"Evaluation time {curr_ts.isoformat()} is earlier than last scaling time {self.last_scaling_time.isoformat()}"
            )

        remaining = max(0.0, self.cooldown_seconds - elapsed)
        is_active = remaining > 0.0

        # Cooldown only blocks recommendations that would change replicas
        wants_change = candidate_replicas != current_replicas
        is_blocked = is_active and wants_change

        effective_replicas = current_replicas if is_blocked else candidate_replicas
        reason_override = DecisionReason.COOLDOWN_ACTIVE if is_blocked else None

        details = {
            "cooldown_seconds": self.cooldown_seconds,
            "elapsed_seconds": elapsed,
            "remaining_seconds": remaining,
            "wants_change": wants_change,
        }

        return CooldownEvaluation(
            is_active=is_active,
            is_blocked=is_blocked,
            remaining_seconds=remaining,
            last_scaling_time=self.last_scaling_time,
            effective_replicas=effective_replicas,
            reason_override=reason_override,
            details=details,
        )

    def record_decision(
        self,
        decision_time: datetime,
        final_replicas: int,
        current_replicas: int,
    ) -> None:
        """Record an accepted decision. If replicas change, cooldown window resets.

        Parameters
        ----------
        decision_time : datetime
            Timestamp of accepted decision.
        final_replicas : int
            Final accepted replica count.
        current_replicas : int
            Current replica count.
        """
        dt = require_timezone_aware(decision_time)
        if final_replicas != current_replicas:
            self.last_scaling_time = dt
            self.last_scaling_replicas = final_replicas

    def reset(self) -> None:
        """Reset cooldown tracking state."""
        self.last_scaling_time = None
        self.last_scaling_replicas = None
