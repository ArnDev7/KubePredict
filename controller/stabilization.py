"""Downscale stabilization window for KubePredict controller (Phase 10B).

Dampens downscale decisions by inspecting a bounded sliding window of recent
recommendations and selecting the safest (maximum) recommendation within the
stabilization window. Does not affect scale-up decisions. Operates strictly in dry-run
memory state without modifying cluster HPA manifests.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from shared.enums import DecisionReason
from shared.validation import require_timezone_aware


@dataclass(frozen=True)
class StabilizationEvaluation:
    """Outcome of evaluating downscale stabilization against a candidate recommendation."""

    is_stabilized: bool
    candidate_replicas: int
    stabilized_replicas: int
    window_seconds: float
    window_samples: int
    window_max_replicas: int | None
    reason_override: DecisionReason | None
    details: dict[str, Any]


class DownscaleStabilizer:
    """Manages recommendation history and applies stabilization window to downscale requests."""

    def __init__(self, stabilization_window_seconds: float) -> None:
        if stabilization_window_seconds < 0.0:
            raise ValueError(
                f"stabilization_window_seconds must be >= 0.0, got {stabilization_window_seconds}"
            )
        self.window_seconds = float(stabilization_window_seconds)
        # Store as list of tuples (timestamp, recommendation)
        self._history: list[tuple[datetime, int]] = []

    def evaluate(
        self,
        current_time: datetime,
        candidate_replicas: int,
        current_replicas: int,
    ) -> StabilizationEvaluation:
        """Evaluate candidate recommendation against the downscale stabilization window.

        Parameters
        ----------
        current_time : datetime
            Time of evaluation.
        candidate_replicas : int
            Candidate replica recommendation (after bounds and step limits).
        current_replicas : int
            Current actual replica count.

        Returns
        -------
        StabilizationEvaluation
            Stabilization outcome and effective recommendation.
        """
        curr_ts = require_timezone_aware(current_time)

        # 1. Prune historical entries outside window and future entries
        cutoff = curr_ts - timedelta(seconds=self.window_seconds)
        self._history = [(ts, rec) for ts, rec in self._history if cutoff <= ts <= curr_ts]

        # 2. Scale-up or no-change is unaffected by downscale stabilization
        if candidate_replicas >= current_replicas or self.window_seconds <= 0.0:
            return StabilizationEvaluation(
                is_stabilized=False,
                candidate_replicas=candidate_replicas,
                stabilized_replicas=candidate_replicas,
                window_seconds=self.window_seconds,
                window_samples=len(self._history),
                window_max_replicas=None,
                reason_override=None,
                details={"window_seconds": self.window_seconds, "is_downscale": False},
            )

        # 3. For downscale (candidate_replicas < current_replicas):
        # Inspect past recommendations in window
        if not self._history:
            return StabilizationEvaluation(
                is_stabilized=False,
                candidate_replicas=candidate_replicas,
                stabilized_replicas=candidate_replicas,
                window_seconds=self.window_seconds,
                window_samples=0,
                window_max_replicas=None,
                reason_override=None,
                details={"window_seconds": self.window_seconds, "history_empty": True},
            )

        window_recs = [rec for _, rec in self._history]
        window_max = max(window_recs)

        # Safest higher replica recommendation within window
        if window_max > candidate_replicas:
            # Bound stabilized recommendation so it never increases replicas above current
            stabilized = min(current_replicas, window_max)
            is_stabilized = stabilized > candidate_replicas
            reason_override = DecisionReason.DOWNSCALE_STABILIZED if is_stabilized else None
        else:
            stabilized = candidate_replicas
            is_stabilized = False
            reason_override = None

        details = {
            "window_seconds": self.window_seconds,
            "window_samples": len(self._history),
            "window_max": window_max,
            "is_downscale": True,
        }

        return StabilizationEvaluation(
            is_stabilized=is_stabilized,
            candidate_replicas=candidate_replicas,
            stabilized_replicas=stabilized,
            window_seconds=self.window_seconds,
            window_samples=len(self._history),
            window_max_replicas=window_max,
            reason_override=reason_override,
            details=details,
        )

    def record_recommendation(
        self,
        decision_time: datetime,
        recommendation: int,
    ) -> None:
        """Add an evaluated recommendation to the stabilization history.

        Parameters
        ----------
        decision_time : datetime
            Time of decision.
        recommendation : int
            Selected replica recommendation.
        """
        ts = require_timezone_aware(decision_time)
        # Check duplicate timestamp
        for existing_ts, existing_rec in self._history:
            if existing_ts == ts:
                if existing_rec != recommendation:
                    raise ValueError(
                        f"Duplicate timestamp {ts.isoformat()} with conflicting recommendation ({existing_rec} vs {recommendation})"
                    )
                return

        self._history.append((ts, recommendation))

    def reset(self) -> None:
        """Clear stabilization history."""
        self._history.clear()
