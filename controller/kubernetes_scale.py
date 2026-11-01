"""Narrowly scoped, secure Kubernetes scale subresource client (Phase 11).

Strictly restricted to:
- Namespace: kubepredict-system
- Deployment: kubepredict-workload-api
- Scale subresource only: read_namespaced_deployment_scale, patch_namespaced_deployment_scale

Requires dual live-safety switches:
- CONTROLLER_MODE == "live"
- ENABLE_LIVE_SCALING == True

Zero mutation of ordinary Deployment, pods, HPAs, secrets, or cluster resources.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from controller.arbitration_decision import ArbitrationDecisionRecord
from shared.enums import DecisionReason, PolicyMode
from shared.validation import require_timezone_aware

logger = logging.getLogger(__name__)

# Hard-restricted target constants (NEVER configurable via env or CLI)
TARGET_NAMESPACE: str = "kubepredict-system"
TARGET_DEPLOYMENT: str = "kubepredict-workload-api"

# Default safety limits
MAX_DECISION_AGE_SECONDS: float = 60.0
DEFAULT_TIMEOUT_SECONDS: float = 10.0
MAX_RETRIES: int = 3
INITIAL_BACKOFF_SECONDS: float = 0.2
BACKOFF_MULTIPLIER: float = 2.0


class ScaleWriteResult(BaseModel):
    """Structured contract recording scale subresource write attempts and outcomes."""

    attempted_at: datetime
    completed_at: datetime
    decision_id: str
    target_namespace: str = TARGET_NAMESPACE
    target_deployment: str = TARGET_DEPLOYMENT
    previous_replicas: int
    requested_replicas: int
    observed_replicas_after: int | None = None
    attempted: bool
    succeeded: bool
    rejected: bool
    rejection_reason: str | None = None
    api_status: int | None = None
    retry_count: int = 0
    conflict_count: int = 0
    duration_ms: float = 0.0
    warnings: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class KubernetesScaleClient:
    """Least-privilege scale subresource client for the KubePredict controller."""

    def __init__(
        self,
        api_client: Any = None,
        apps_v1_api: Any = None,
        autoscaling_v1_api: Any = None,
        controller_mode: str = "dry_run",
        enable_live_scaling: bool = False,
        min_replicas: int = 1,
        max_replicas: int = 10,
        max_decision_age_seconds: float = MAX_DECISION_AGE_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._controller_mode = controller_mode.strip().lower()
        self._enable_live_scaling = bool(enable_live_scaling)
        self._min_replicas = min_replicas
        self._max_replicas = max_replicas
        self._max_decision_age_seconds = max_decision_age_seconds
        self._timeout_seconds = timeout_seconds

        # In-process single-writer lock
        self._write_lock = threading.Lock()
        self._executed_decision_ids: set[str] = set()

        self._apps_v1 = apps_v1_api
        self._autoscaling_v1 = autoscaling_v1_api
        self._initialized = False

        if apps_v1_api is not None:
            self._initialized = True

    def _ensure_api_initialized(self) -> None:
        """Lazily initialize Kubernetes API client if not injected."""
        if self._initialized:
            return

        from kubernetes import client, config

        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes configuration")
        except Exception:  # noqa: BLE001
            try:
                config.load_kube_config(context="kind-kubepredict-dev")
                logger.info("Loaded local kubeconfig for context kind-kubepredict-dev")
            except Exception as e:
                logger.warning(
                    "Could not initialize Kubernetes client from in-cluster or kubeconfig: %s", e
                )
                raise RuntimeError(f"Failed to initialize Kubernetes client: {e}") from e

        self._apps_v1 = client.AppsV1Api()
        try:
            self._autoscaling_v1 = client.AutoscalingV1Api()
        except Exception:  # noqa: BLE001
            self._autoscaling_v1 = None
        self._initialized = True

    @property
    def is_live_enabled(self) -> bool:
        """Return True only if BOTH live scaling switches are satisfied."""
        return self._controller_mode == "live" and self._enable_live_scaling is True

    def read_workload_scale(self) -> tuple[int | None, str | None]:
        """Read the scale subresource of the hardcoded workload deployment.

        Returns
        -------
        tuple[int | None, str | None]
            (current_replicas, error_message)
        """
        try:
            self._ensure_api_initialized()
            scale = self._apps_v1.read_namespaced_deployment_scale(
                name=TARGET_DEPLOYMENT,
                namespace=TARGET_NAMESPACE,
                _request_timeout=self._timeout_seconds,
            )
            replicas = scale.spec.replicas
            return replicas, None
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Failed to read scale subresource for %s/%s: %s",
                TARGET_NAMESPACE,
                TARGET_DEPLOYMENT,
                e,
            )
            return None, str(e)

    def check_live_hpa_exists(self) -> bool:
        """Check whether any HorizontalPodAutoscaler exists in the target namespace."""
        try:
            self._ensure_api_initialized()
            if self._autoscaling_v1:
                hpas = self._autoscaling_v1.list_namespaced_horizontal_pod_autoscaler(
                    namespace=TARGET_NAMESPACE,
                    _request_timeout=self._timeout_seconds,
                )
                return len(hpas.items) > 0
            return False
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not list HPAs in %s: %s", TARGET_NAMESPACE, e)
            return False

    def scale_workload(
        self,
        decision: ArbitrationDecisionRecord,
        current_time: datetime | None = None,
    ) -> ScaleWriteResult:
        """Execute or reject a scaling decision against the workload scale subresource.

        Parameters
        ----------
        decision : ArbitrationDecisionRecord
            The arbitrated dry-run decision to evaluate.
        current_time : datetime | None
            Evaluation timestamp (defaults to datetime.now(UTC)).

        Returns
        -------
        ScaleWriteResult
            Structured record of the attempt, rejection, or execution.
        """
        now = require_timezone_aware(current_time or datetime.now(UTC))
        attempt_start = now
        decision_id = f"cycle-{decision.cycle_number}-{decision.timestamp.isoformat()}"

        requested = decision.final_recommendation
        previous = decision.current_replicas

        # If final recommendation is missing
        if requested is None:
            return ScaleWriteResult(
                attempted_at=attempt_start,
                completed_at=require_timezone_aware(datetime.now(UTC)),
                decision_id=decision_id,
                previous_replicas=previous,
                requested_replicas=previous,
                attempted=False,
                succeeded=False,
                rejected=True,
                rejection_reason="no_recommendation",
                duration_ms=0.0,
            )

        # Precondition 1: Dual safety switches
        if not self.is_live_enabled:
            reason = (
                "live_mode_disabled" if self._controller_mode != "live" else "live_scaling_disabled"
            )
            return ScaleWriteResult(
                attempted_at=attempt_start,
                completed_at=require_timezone_aware(datetime.now(UTC)),
                decision_id=decision_id,
                previous_replicas=previous,
                requested_replicas=requested,
                attempted=False,
                succeeded=False,
                rejected=True,
                rejection_reason=reason,
                duration_ms=0.0,
            )

        # Precondition 2: Policy mode validity
        if decision.policy_mode == PolicyMode.NO_RECOMMENDATION:
            return ScaleWriteResult(
                attempted_at=attempt_start,
                completed_at=require_timezone_aware(datetime.now(UTC)),
                decision_id=decision_id,
                previous_replicas=previous,
                requested_replicas=requested,
                attempted=False,
                succeeded=False,
                rejected=True,
                rejection_reason="no_recommendation_mode",
                duration_ms=0.0,
            )

        # Precondition 3: Replica bounds check
        if requested < self._min_replicas or requested > self._max_replicas:
            return ScaleWriteResult(
                attempted_at=attempt_start,
                completed_at=require_timezone_aware(datetime.now(UTC)),
                decision_id=decision_id,
                previous_replicas=previous,
                requested_replicas=requested,
                attempted=False,
                succeeded=False,
                rejected=True,
                rejection_reason="bounds_exceeded",
                duration_ms=0.0,
            )

        # Precondition 4: No change required
        if requested == previous:
            return ScaleWriteResult(
                attempted_at=attempt_start,
                completed_at=require_timezone_aware(datetime.now(UTC)),
                decision_id=decision_id,
                previous_replicas=previous,
                requested_replicas=requested,
                attempted=False,
                succeeded=False,
                rejected=True,
                rejection_reason="no_change_required",
                duration_ms=0.0,
            )

        # Precondition 5: Decision age freshness
        decision_age = (now - decision.timestamp).total_seconds()
        if decision_age < 0 or decision_age > self._max_decision_age_seconds:
            return ScaleWriteResult(
                attempted_at=attempt_start,
                completed_at=require_timezone_aware(datetime.now(UTC)),
                decision_id=decision_id,
                previous_replicas=previous,
                requested_replicas=requested,
                attempted=False,
                succeeded=False,
                rejected=True,
                rejection_reason="stale_decision",
                duration_ms=0.0,
                warnings=[
                    f"Decision age {decision_age:.1f}s exceeded max {self._max_decision_age_seconds}s"
                ],
            )

        # Precondition 6: Cooldown blocked
        if decision.cooldown_blocked or decision.primary_reason == DecisionReason.COOLDOWN_ACTIVE:
            return ScaleWriteResult(
                attempted_at=attempt_start,
                completed_at=require_timezone_aware(datetime.now(UTC)),
                decision_id=decision_id,
                previous_replicas=previous,
                requested_replicas=requested,
                attempted=False,
                succeeded=False,
                rejected=True,
                rejection_reason="cooldown_active",
                duration_ms=0.0,
            )

        # Precondition 7: Duplicate decision execution check
        with self._write_lock:
            if decision_id in self._executed_decision_ids:
                return ScaleWriteResult(
                    attempted_at=attempt_start,
                    completed_at=require_timezone_aware(datetime.now(UTC)),
                    decision_id=decision_id,
                    previous_replicas=previous,
                    requested_replicas=requested,
                    attempted=False,
                    succeeded=False,
                    rejected=True,
                    rejection_reason="duplicate_decision",
                    duration_ms=0.0,
                )

        # Precondition 8: No live HPA check
        if self.check_live_hpa_exists():
            return ScaleWriteResult(
                attempted_at=attempt_start,
                completed_at=require_timezone_aware(datetime.now(UTC)),
                decision_id=decision_id,
                previous_replicas=previous,
                requested_replicas=requested,
                attempted=False,
                succeeded=False,
                rejected=True,
                rejection_reason="hpa_detected",
                duration_ms=0.0,
                warnings=["Live HPA is present in target namespace; aborting controller write"],
            )

        # Precondition 9: Verify current cluster replicas match decision input
        current_k8s_replicas, read_err = self.read_workload_scale()
        if read_err or current_k8s_replicas is None:
            return ScaleWriteResult(
                attempted_at=attempt_start,
                completed_at=require_timezone_aware(datetime.now(UTC)),
                decision_id=decision_id,
                previous_replicas=previous,
                requested_replicas=requested,
                attempted=False,
                succeeded=False,
                rejected=True,
                rejection_reason="read_scale_failed",
                duration_ms=0.0,
                warnings=[f"Could not verify cluster replicas: {read_err}"],
            )

        if current_k8s_replicas != previous:
            return ScaleWriteResult(
                attempted_at=attempt_start,
                completed_at=require_timezone_aware(datetime.now(UTC)),
                decision_id=decision_id,
                previous_replicas=previous,
                requested_replicas=requested,
                observed_replicas_after=current_k8s_replicas,
                attempted=False,
                succeeded=False,
                rejected=True,
                rejection_reason="replica_mismatch",
                duration_ms=0.0,
                warnings=[
                    f"Cluster replicas ({current_k8s_replicas}) differ from decision input ({previous})"
                ],
            )

        # All preconditions passed -> Execute scale patch with retry and 409 conflict handling
        t0 = time.perf_counter()
        retries = 0
        conflicts = 0
        patch_body = {"spec": {"replicas": requested}}

        with self._write_lock:
            for attempt in range(MAX_RETRIES + 1):
                try:
                    self._apps_v1.patch_namespaced_deployment_scale(
                        name=TARGET_DEPLOYMENT,
                        namespace=TARGET_NAMESPACE,
                        body=patch_body,
                        _request_timeout=self._timeout_seconds,
                    )

                    # Verify replica state after successful patch
                    observed_after, _ = self.read_workload_scale()
                    duration_ms = (time.perf_counter() - t0) * 1000.0

                    self._executed_decision_ids.add(decision_id)
                    logger.info(
                        "Successfully scaled %s/%s from %d to %d replicas (observed: %s)",
                        TARGET_NAMESPACE,
                        TARGET_DEPLOYMENT,
                        previous,
                        requested,
                        observed_after,
                    )

                    return ScaleWriteResult(
                        attempted_at=attempt_start,
                        completed_at=require_timezone_aware(datetime.now(UTC)),
                        decision_id=decision_id,
                        previous_replicas=previous,
                        requested_replicas=requested,
                        observed_replicas_after=observed_after,
                        attempted=True,
                        succeeded=True,
                        rejected=False,
                        api_status=200,
                        retry_count=retries,
                        conflict_count=conflicts,
                        duration_ms=duration_ms,
                    )

                except Exception as e:  # noqa: BLE001
                    # Check for HTTP 409 conflict
                    status_code = getattr(e, "status", None)
                    if status_code == 409:
                        conflicts += 1
                        logger.warning(
                            "Conflict (409) patching scale for %s/%s: %s",
                            TARGET_NAMESPACE,
                            TARGET_DEPLOYMENT,
                            e,
                        )
                        # Re-read scale before retry
                        latest_reps, _ = self.read_workload_scale()
                        if latest_reps == requested:
                            # Already scaled to requested
                            duration_ms = (time.perf_counter() - t0) * 1000.0
                            self._executed_decision_ids.add(decision_id)
                            return ScaleWriteResult(
                                attempted_at=attempt_start,
                                completed_at=require_timezone_aware(datetime.now(UTC)),
                                decision_id=decision_id,
                                previous_replicas=previous,
                                requested_replicas=requested,
                                observed_replicas_after=latest_reps,
                                attempted=True,
                                succeeded=True,
                                rejected=False,
                                api_status=409,
                                retry_count=retries,
                                conflict_count=conflicts,
                                duration_ms=duration_ms,
                                warnings=[
                                    "Replicas already matched requested count following 409 conflict"
                                ],
                            )

                    if attempt < MAX_RETRIES:
                        retries += 1
                        sleep_time = INITIAL_BACKOFF_SECONDS * (BACKOFF_MULTIPLIER**attempt)
                        logger.info(
                            "Retrying scale patch in %.2fs (attempt %d/%d)",
                            sleep_time,
                            attempt + 1,
                            MAX_RETRIES,
                        )
                        time.sleep(sleep_time)
                    else:
                        duration_ms = (time.perf_counter() - t0) * 1000.0
                        logger.error(
                            "Exhausted retries patching scale for %s/%s: %s",
                            TARGET_NAMESPACE,
                            TARGET_DEPLOYMENT,
                            e,
                        )
                        return ScaleWriteResult(
                            attempted_at=attempt_start,
                            completed_at=require_timezone_aware(datetime.now(UTC)),
                            decision_id=decision_id,
                            previous_replicas=previous,
                            requested_replicas=requested,
                            attempted=True,
                            succeeded=False,
                            rejected=True,
                            rejection_reason="api_error",
                            api_status=status_code,
                            retry_count=retries,
                            conflict_count=conflicts,
                            duration_ms=duration_ms,
                            warnings=[f"API write failed: {e}"],
                        )

        # Fallback error record
        return ScaleWriteResult(
            attempted_at=attempt_start,
            completed_at=require_timezone_aware(datetime.now(UTC)),
            decision_id=decision_id,
            previous_replicas=previous,
            requested_replicas=requested,
            attempted=True,
            succeeded=False,
            rejected=True,
            rejection_reason="unexpected_failure",
            duration_ms=(time.perf_counter() - t0) * 1000.0,
        )
