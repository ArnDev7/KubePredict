"""Unit tests for Kubernetes scale client, write preconditions, and safety boundaries."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from controller.arbitration_decision import ArbitrationDecisionRecord
from controller.kubernetes_scale import (
    TARGET_DEPLOYMENT,
    TARGET_NAMESPACE,
    KubernetesScaleClient,
)
from shared.enums import DecisionReason, PolicyMode, ScalingDirection, TrustOutcome
from shared.models import ForecastResult, ForecastTrustDecision


def _make_trust(trusted: bool = True) -> ForecastTrustDecision:
    now = datetime.now(UTC)
    return ForecastTrustDecision(
        evaluated_at=now,
        trusted=trusted,
        outcome=TrustOutcome.TRUSTED if trusted else TrustOutcome.INSUFFICIENT_RESIDUAL_HISTORY,
        forecast_id="fc-test-001" if trusted else None,
        model_name="persistence",
        telemetry_fresh=True,
        history_observations=5,
        minimum_history_required=1,
        matched_residual_count=5 if trusted else 0,
        minimum_matched_residuals=3,
        recent_mae=0.5 if trusted else None,
        recent_smape=4.0 if trusted else None,
        absolute_error_threshold=10.0,
        relative_error_threshold=50.0,
        evidence_window_seconds=180.0,
    )


def _make_forecast() -> ForecastResult:
    return ForecastResult(
        generated_at=datetime.now(UTC),
        horizon_seconds=15,
        predicted_request_rate_per_second=10.0,
        confidence=0.9,
        model_name="persistence",
        observations_used=5,
    )


def _create_decision(
    cycle_number: int = 1,
    current_replicas: int = 2,
    final_recommendation: int | None = 3,
    policy_mode: PolicyMode = PolicyMode.PREDICTIVE,
    primary_reason: DecisionReason = DecisionReason.TRUSTED_FORECAST,
    cooldown_blocked: bool = False,
    age_seconds: float = 5.0,
) -> ArbitrationDecisionRecord:
    ts = datetime.now(UTC) - timedelta(seconds=age_seconds)
    direction = ScalingDirection.NO_CHANGE
    if final_recommendation is not None:
        if final_recommendation > current_replicas:
            direction = ScalingDirection.SCALE_UP
        elif final_recommendation < current_replicas:
            direction = ScalingDirection.SCALE_DOWN

    # Build mode-appropriate trust decision
    if policy_mode == PolicyMode.PREDICTIVE:
        trust = _make_trust(trusted=True)
        forecast = _make_forecast()
    elif policy_mode == PolicyMode.REACTIVE_FALLBACK:
        trust = _make_trust(trusted=False)
        forecast = _make_forecast()
    else:
        trust = None
        forecast = None

    return ArbitrationDecisionRecord(
        timestamp=ts,
        cycle_number=cycle_number,
        current_replicas=current_replicas,
        policy_mode=policy_mode,
        primary_reason=primary_reason,
        telemetry_status="valid",
        forecast=forecast,
        trust_decision=trust,
        final_recommendation=final_recommendation,
        direction=direction,
        cooldown_blocked=cooldown_blocked,
        action_executed=False,
    )


class TestKubernetesScaleClient:
    """Verify write preconditions, bounds checking, conflict handling, and safety switches."""

    def test_safe_defaults_reject_write(self) -> None:
        mock_apps = MagicMock()
        client = KubernetesScaleClient(apps_v1_api=mock_apps)

        decision = _create_decision(final_recommendation=3)
        res = client.scale_workload(decision)

        assert res.attempted is False
        assert res.succeeded is False
        assert res.rejected is True
        assert res.rejection_reason in ("live_mode_disabled", "live_scaling_disabled")
        mock_apps.patch_namespaced_deployment_scale.assert_not_called()

    def test_single_switch_enabled_still_rejects(self) -> None:
        mock_apps = MagicMock()

        # Mode live but enable_live_scaling False
        client1 = KubernetesScaleClient(
            apps_v1_api=mock_apps, controller_mode="live", enable_live_scaling=False
        )
        res1 = client1.scale_workload(_create_decision(final_recommendation=3))
        assert res1.rejected is True
        assert res1.rejection_reason == "live_scaling_disabled"

        # Mode dry_run but enable_live_scaling True
        client2 = KubernetesScaleClient(
            apps_v1_api=mock_apps, controller_mode="dry_run", enable_live_scaling=True
        )
        res2 = client2.scale_workload(_create_decision(final_recommendation=3))
        assert res2.rejected is True
        assert res2.rejection_reason == "live_mode_disabled"

        mock_apps.patch_namespaced_deployment_scale.assert_not_called()

    def test_no_recommendation_rejected(self) -> None:
        mock_apps = MagicMock()
        client = KubernetesScaleClient(
            apps_v1_api=mock_apps, controller_mode="live", enable_live_scaling=True
        )

        decision = _create_decision(
            final_recommendation=None,
            policy_mode=PolicyMode.NO_RECOMMENDATION,
            primary_reason=DecisionReason.TELEMETRY_UNAVAILABLE,
        )
        res = client.scale_workload(decision)

        assert res.rejected is True
        assert res.rejection_reason == "no_recommendation"
        mock_apps.patch_namespaced_deployment_scale.assert_not_called()

    def test_bounds_exceeded_rejected(self) -> None:
        mock_apps = MagicMock()
        client = KubernetesScaleClient(
            apps_v1_api=mock_apps,
            controller_mode="live",
            enable_live_scaling=True,
            min_replicas=3,
            max_replicas=10,
        )

        # Test upper bound: recommendation exceeds max
        decision_high = _create_decision(current_replicas=2, final_recommendation=15)
        res_high = client.scale_workload(decision_high)
        assert res_high.rejected is True
        assert res_high.rejection_reason == "bounds_exceeded"

        # Test lower bound: recommendation below min (2 < min_replicas=3)
        decision_low = _create_decision(
            current_replicas=4,
            final_recommendation=2,
            policy_mode=PolicyMode.REACTIVE_FALLBACK,
            primary_reason=DecisionReason.REACTIVE_FALLBACK,
        )
        res_low = client.scale_workload(decision_low)
        assert res_low.rejected is True
        assert res_low.rejection_reason == "bounds_exceeded"

        mock_apps.patch_namespaced_deployment_scale.assert_not_called()

    def test_no_change_required_rejected(self) -> None:
        mock_apps = MagicMock()
        client = KubernetesScaleClient(
            apps_v1_api=mock_apps, controller_mode="live", enable_live_scaling=True
        )

        decision = _create_decision(current_replicas=2, final_recommendation=2)
        res = client.scale_workload(decision)

        assert res.rejected is True
        assert res.rejection_reason == "no_change_required"
        mock_apps.patch_namespaced_deployment_scale.assert_not_called()

    def test_stale_decision_rejected(self) -> None:
        mock_apps = MagicMock()
        client = KubernetesScaleClient(
            apps_v1_api=mock_apps,
            controller_mode="live",
            enable_live_scaling=True,
            max_decision_age_seconds=30.0,
        )

        # Decision generated 65 seconds ago
        decision = _create_decision(final_recommendation=3, age_seconds=65.0)
        res = client.scale_workload(decision)

        assert res.rejected is True
        assert res.rejection_reason == "stale_decision"
        mock_apps.patch_namespaced_deployment_scale.assert_not_called()

    def test_cooldown_blocked_rejected(self) -> None:
        mock_apps = MagicMock()
        client = KubernetesScaleClient(
            apps_v1_api=mock_apps, controller_mode="live", enable_live_scaling=True
        )

        decision = _create_decision(final_recommendation=3, cooldown_blocked=True)
        res = client.scale_workload(decision)

        assert res.rejected is True
        assert res.rejection_reason == "cooldown_active"
        mock_apps.patch_namespaced_deployment_scale.assert_not_called()

    def test_duplicate_decision_rejected(self) -> None:
        mock_apps = MagicMock()
        mock_scale = MagicMock()
        mock_scale.spec.replicas = 2
        mock_apps.read_namespaced_deployment_scale.return_value = mock_scale

        client = KubernetesScaleClient(
            apps_v1_api=mock_apps, controller_mode="live", enable_live_scaling=True
        )
        decision = _create_decision(cycle_number=42, final_recommendation=3)

        # First attempt succeeds
        res1 = client.scale_workload(decision)
        assert res1.succeeded is True

        # Second attempt with identical decision is rejected
        res2 = client.scale_workload(decision)
        assert res2.rejected is True
        assert res2.rejection_reason == "duplicate_decision"

    def test_hpa_detected_rejects_write(self) -> None:
        mock_apps = MagicMock()
        mock_auto = MagicMock()
        mock_hpa_item = MagicMock()
        mock_auto.list_namespaced_horizontal_pod_autoscaler.return_value = MagicMock(
            items=[mock_hpa_item]
        )

        client = KubernetesScaleClient(
            apps_v1_api=mock_apps,
            autoscaling_v1_api=mock_auto,
            controller_mode="live",
            enable_live_scaling=True,
        )

        decision = _create_decision(final_recommendation=3)
        res = client.scale_workload(decision)

        assert res.rejected is True
        assert res.rejection_reason == "hpa_detected"
        mock_apps.patch_namespaced_deployment_scale.assert_not_called()

    def test_replica_mismatch_rejected(self) -> None:
        mock_apps = MagicMock()
        # Decision assumes current replicas is 2, but cluster reports 4
        mock_scale = MagicMock()
        mock_scale.spec.replicas = 4
        mock_apps.read_namespaced_deployment_scale.return_value = mock_scale

        client = KubernetesScaleClient(
            apps_v1_api=mock_apps, controller_mode="live", enable_live_scaling=True
        )
        decision = _create_decision(current_replicas=2, final_recommendation=3)

        res = client.scale_workload(decision)
        assert res.rejected is True
        assert res.rejection_reason == "replica_mismatch"
        mock_apps.patch_namespaced_deployment_scale.assert_not_called()

    def test_successful_scale_patch(self) -> None:
        mock_apps = MagicMock()
        mock_scale = MagicMock()
        mock_scale.spec.replicas = 2
        mock_apps.read_namespaced_deployment_scale.return_value = mock_scale

        client = KubernetesScaleClient(
            apps_v1_api=mock_apps, controller_mode="live", enable_live_scaling=True
        )
        decision = _create_decision(current_replicas=2, final_recommendation=4)

        res = client.scale_workload(decision)

        assert res.attempted is True
        assert res.succeeded is True
        assert res.rejected is False
        assert res.api_status == 200
        assert res.previous_replicas == 2
        assert res.requested_replicas == 4

        mock_apps.patch_namespaced_deployment_scale.assert_called_once_with(
            name=TARGET_DEPLOYMENT,
            namespace=TARGET_NAMESPACE,
            body={"spec": {"replicas": 4}},
            _request_timeout=10.0,
        )

    def test_409_conflict_retry_handling(self) -> None:
        mock_apps = MagicMock()
        mock_scale = MagicMock()
        mock_scale.spec.replicas = 2

        # Create conflict exception with status=409
        class ConflictException(Exception):
            status = 409

        # First patch call raises 409, second succeeds
        mock_apps.read_namespaced_deployment_scale.return_value = mock_scale
        mock_apps.patch_namespaced_deployment_scale.side_effect = [
            ConflictException("Resource modified"),
            None,
        ]

        client = KubernetesScaleClient(
            apps_v1_api=mock_apps, controller_mode="live", enable_live_scaling=True
        )
        decision = _create_decision(current_replicas=2, final_recommendation=3)

        res = client.scale_workload(decision)
        assert res.succeeded is True
        assert res.conflict_count == 1
        assert res.retry_count == 1

    def test_api_retry_exhaustion(self) -> None:
        mock_apps = MagicMock()
        mock_scale = MagicMock()
        mock_scale.spec.replicas = 2
        mock_apps.read_namespaced_deployment_scale.return_value = mock_scale
        mock_apps.patch_namespaced_deployment_scale.side_effect = RuntimeError(
            "Persistent API error"
        )

        client = KubernetesScaleClient(
            apps_v1_api=mock_apps, controller_mode="live", enable_live_scaling=True
        )
        decision = _create_decision(current_replicas=2, final_recommendation=3)

        res = client.scale_workload(decision)
        assert res.succeeded is False
        assert res.rejected is True
        assert res.rejection_reason == "api_error"
        assert res.retry_count == 3
