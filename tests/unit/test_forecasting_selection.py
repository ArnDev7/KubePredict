"""Unit tests for model ranking, parameter selection, and provisional candidate logic."""

import json
from datetime import UTC, datetime

import pytest

from forecasting.rolling_evaluation import EvaluationMetricRow
from forecasting.selection import (
    rank_validation_configurations,
    select_best_parameters_per_family,
    select_provisional_candidates,
)


def make_metric_row(
    model_name: str,
    parameters: dict,
    horizon: int,
    scenario: str,
    mae: float,
    rmse: float,
    smape: float,
    valid_count: int = 5,
) -> EvaluationMetricRow:
    """Helper to construct an EvaluationMetricRow for testing selection logic."""
    return EvaluationMetricRow(
        model_name=model_name,
        parameters_json=json.dumps(parameters, sort_keys=True),
        scenario_name=scenario,
        horizon_steps=horizon,
        split="validation",
        mae=mae,
        rmse=rmse,
        smape=smape,
        bias=0.0,
        valid_count=valid_count,
        excluded_count=0,
    )


def test_parameter_selection_best_validation() -> None:
    """Verify select_best_parameters_per_family selects parameter with lowest validation MAE."""
    rows = [
        make_metric_row(
            "moving_average", {"window": 3}, 1, "scen_a", mae=5.0, rmse=6.0, smape=10.0
        ),
        make_metric_row("moving_average", {"window": 5}, 1, "scen_a", mae=3.0, rmse=4.0, smape=8.0),
        make_metric_row(
            "moving_average", {"window": 8}, 1, "scen_a", mae=7.0, rmse=8.0, smape=15.0
        ),
    ]

    selected = select_best_parameters_per_family(rows, primary_metric="mae")
    assert selected[("moving_average", 1)] == {"window": 5}


def test_ranking_orders_by_primary_and_secondary_metrics() -> None:
    """Verify ranking orders by primary metric (MAE), then secondary (RMSE)."""
    rows = [
        make_metric_row("ewma", {"alpha": 0.2}, 1, "scen_a", mae=4.0, rmse=5.0, smape=10.0),
        make_metric_row("ewma", {"alpha": 0.4}, 1, "scen_a", mae=2.0, rmse=3.0, smape=5.0),
        make_metric_row(
            "ewma", {"alpha": 0.6}, 1, "scen_a", mae=2.0, rmse=2.5, smape=5.0
        ),  # better RMSE
    ]

    rankings = rank_validation_configurations(rows, primary_metric="mae")
    assert rankings[0].parameters_json == json.dumps({"alpha": 0.6})
    assert rankings[0].rank == 1
    assert rankings[1].parameters_json == json.dumps({"alpha": 0.4})
    assert rankings[1].rank == 2


def test_provisional_candidate_selection_when_non_naive_beats_persistence() -> None:
    """Verify non-naive model is selected when it outperforms persistence."""
    rows = [
        make_metric_row("persistence", {}, 1, "scen_a", mae=10.0, rmse=12.0, smape=20.0),
        make_metric_row("ewma", {"alpha": 0.5}, 1, "scen_a", mae=7.0, rmse=9.0, smape=15.0),
    ]

    report = select_provisional_candidates(rows, generated_at=datetime.now(UTC).isoformat())
    cand = report.per_horizon[0]
    assert cand.horizon_steps == 1
    assert cand.model_name == "ewma"
    assert cand.status == "provisional_candidate"
    assert cand.mae_diff_vs_persistence == pytest.approx(-3.0)


def test_provisional_candidate_selection_retains_persistence_when_no_model_beats_it() -> None:
    """Verify persistence is retained when non-naive models have higher MAE than persistence."""
    rows = [
        make_metric_row("persistence", {}, 1, "scen_a", mae=5.0, rmse=6.0, smape=10.0),
        make_metric_row(
            "moving_average", {"window": 3}, 1, "scen_a", mae=8.0, rmse=9.0, smape=16.0
        ),
        make_metric_row(
            "linear_trend", {"window": 5}, 1, "scen_a", mae=12.0, rmse=14.0, smape=25.0
        ),
    ]

    report = select_provisional_candidates(rows, generated_at=datetime.now(UTC).isoformat())
    cand = report.per_horizon[0]
    assert cand.horizon_steps == 1
    assert cand.model_name == "persistence"
    assert cand.status == "persistence_retained"
    assert "Persistence baseline retained" in cand.selection_reason
