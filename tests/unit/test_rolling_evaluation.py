"""Unit tests for the rolling-origin evaluation engine."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from forecasting.base import ForecastingConfig
from forecasting.persistence import PersistenceForecaster
from forecasting.preprocessing import ScenarioData, partition_scenario
from forecasting.rolling_evaluation import (
    compute_metrics_table,
    evaluate_scenario_split,
)


def create_synthetic_scenario_data(
    sample_count: int = 15,
    missing_indices: list[int] | None = None,
) -> ScenarioData:
    """Helper to create synthetic chronological ScenarioData for testing."""
    base_ts = datetime(2026, 9, 10, 15, 0, 0, tzinfo=UTC)
    ts_list = [base_ts + timedelta(seconds=i * 15) for i in range(sample_count)]
    vals: list[float | None] = [10.0 + float(i * 2) for i in range(sample_count)]
    if missing_indices:
        for idx in missing_indices:
            vals[idx] = None

    return ScenarioData(
        scenario_name="synthetic_test",
        run_id="run-synth-1",
        timestamps=ts_list,
        target_values=vals,
        raw_sample_count=sample_count,
        missing_count=len(missing_indices or []),
        average_interval_seconds=15.0,
        source_file=Path("synthetic.csv"),
    )


def test_rolling_evaluation_clean_series() -> None:
    """Verify rolling-origin predictions on a clean synthetic series."""
    data = create_synthetic_scenario_data(15)
    splits = partition_scenario(data, 0.20, 0.20, 6)
    config = ForecastingConfig()
    model = PersistenceForecaster()

    # Val split has target indices [9, 10, 11]
    # For horizon=1: origin indices are [8, 9, 10]
    preds, excls = evaluate_scenario_split(
        forecaster=model,
        data=data,
        splits=splits,
        horizon=1,
        split_name="validation",
        config=config,
    )

    assert len(excls) == 0
    assert len(preds) == 3

    # Check origin and target indices
    assert [p.origin_index for p in preds] == [8, 9, 10]
    assert [p.target_index for p in preds] == [9, 10, 11]

    # In persistence: pred = actual[origin]. Since y[i] = 10 + 2*i:
    # origin=8 -> y[8] = 26.0 -> pred=26.0. Target=9 -> actual[9] = 28.0.
    # Error = |28 - 26| = 2.0
    for p in preds:
        assert p.predicted_value == pytest.approx(p.actual_value - 2.0)
        assert p.absolute_error == pytest.approx(2.0)


def test_rolling_evaluation_missing_target_excluded() -> None:
    """Verify origin is excluded when target value is missing."""
    # Set index 10 (which is in validation [9, 10, 11]) to None
    data = create_synthetic_scenario_data(15, missing_indices=[10])
    splits = partition_scenario(data, 0.20, 0.20, 6)
    config = ForecastingConfig()
    model = PersistenceForecaster()

    preds, excls = evaluate_scenario_split(
        forecaster=model,
        data=data,
        splits=splits,
        horizon=1,
        split_name="validation",
        config=config,
    )

    # Target 9 is valid.
    # Target 10 has missing target value (excluded).
    # Target 11 has origin 10, which has missing history value (excluded).
    assert len(preds) == 1
    assert len(excls) == 2
    excl_reasons = {e.reason for e in excls}
    assert "missing_target_value" in excl_reasons
    assert "missing_history_value" in excl_reasons


def test_rolling_evaluation_missing_history_excluded() -> None:
    """Verify origin is excluded when required historical value is missing."""
    # Set index 8 to None (which is the origin for target 9 under horizon 1)
    data = create_synthetic_scenario_data(15, missing_indices=[8])
    splits = partition_scenario(data, 0.20, 0.20, 6)
    config = ForecastingConfig()
    model = PersistenceForecaster()

    preds, excls = evaluate_scenario_split(
        forecaster=model,
        data=data,
        splits=splits,
        horizon=1,
        split_name="validation",
        config=config,
    )

    assert len(preds) == 2  # targets 10 and 11
    assert len(excls) == 1
    assert excls[0].origin_index == 8
    assert excls[0].reason == "missing_history_value"


def test_compute_metrics_table() -> None:
    """Verify aggregated metrics table computation."""
    data = create_synthetic_scenario_data(15)
    splits = partition_scenario(data, 0.20, 0.20, 6)
    config = ForecastingConfig()
    model = PersistenceForecaster()

    preds, excls = evaluate_scenario_split(
        forecaster=model,
        data=data,
        splits=splits,
        horizon=1,
        split_name="validation",
        config=config,
    )

    metrics = compute_metrics_table(preds, excls)
    assert len(metrics) == 1
    row = metrics[0]
    assert row.model_name == "persistence"
    assert row.valid_count == 3
    assert row.excluded_count == 0
    assert row.mae == pytest.approx(2.0)
    assert row.rmse == pytest.approx(2.0)
    assert row.bias == pytest.approx(-2.0)
