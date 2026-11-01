"""Unit tests for dry-run controller operational metrics (Phase 9B)."""

from prometheus_client import CollectorRegistry, generate_latest

from controller.metrics import DryRunMetrics


def test_dry_run_metrics_isolated_registry():
    reg1 = CollectorRegistry()
    m1 = DryRunMetrics(registry=reg1)

    reg2 = CollectorRegistry()
    _m2 = DryRunMetrics(registry=reg2)

    # Record on m1 only
    m1.record_recommendation(
        direction="scale_up",
        reason="forecast_demand",
        current_replicas=2,
        proposed_replicas=3,
        final_replicas=3,
        predicted_rate=45.0,
        effective_capacity=15.75,
        constraints=[],
    )

    out1 = generate_latest(reg1).decode("utf-8")
    out2 = generate_latest(reg2).decode("utf-8")

    assert "kubepredict_dry_run_recommendations_total" in out1
    assert 'direction="scale_up"' in out1
    assert 'reason="forecast_demand"' in out1
    assert "kubepredict_dry_run_current_replicas 2.0" in out1
    assert "kubepredict_dry_run_final_replicas 3.0" in out1

    # Reg2 should have zero recommendation counts
    assert "kubepredict_dry_run_recommendations_total" in out2
    assert "kubepredict_dry_run_current_replicas 0.0" in out2


def test_dry_run_metrics_fixed_cardinality_labels():
    reg = CollectorRegistry()
    m = DryRunMetrics(registry=reg)

    # Record valid constraints
    m.record_recommendation(
        direction="scale_up",
        reason="scale_up_step_limited",
        current_replicas=2,
        proposed_replicas=7,
        final_replicas=5,
        predicted_rate=100.0,
        effective_capacity=15.75,
        constraints=["scale_up_step_limited", "maximum_bound"],
    )

    out = generate_latest(reg).decode("utf-8")
    assert 'constraint="scale_up_step_limited"' in out
    assert 'constraint="maximum_bound"' in out

    # Verify absence of forbidden labels
    assert "forecast_id=" not in out
    assert "timestamp=" not in out
    assert "action_success" not in out


def test_dry_run_metrics_unknown_labels_fallbacks():
    reg = CollectorRegistry()
    m = DryRunMetrics(registry=reg)

    # Unknown direction and reason fallback to safe fixed values
    m.record_recommendation(
        direction="invalid_dir",
        reason="invalid_reason",
        current_replicas=2,
        proposed_replicas=2,
        final_replicas=2,
        predicted_rate=20.0,
        effective_capacity=15.75,
        constraints=["unknown_constraint"],
    )

    out = generate_latest(reg).decode("utf-8")
    assert 'direction="no_change"' in out
    assert 'reason="forecast_demand"' in out
    assert 'constraint="unknown_constraint"' not in out
