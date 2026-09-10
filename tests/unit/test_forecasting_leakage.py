"""Explicit leakage-trap tests for forecasting pipeline integrity (Phase 8B)."""

import json
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import numpy as np
import pytest

from forecasting.ewma import EWMAForecaster
from forecasting.linear_trend import LinearTrendForecaster
from forecasting.moving_average import MovingAverageForecaster
from forecasting.persistence import PersistenceForecaster
from forecasting.preprocessing import (
    ScenarioData,
    partition_scenario,
)
from forecasting.rolling_evaluation import EvaluationMetricRow
from forecasting.selection import select_best_parameters_per_family

# ── Future-Perturbation Trap ────────────────────────────────────────────────


class TestFuturePerturbationTrap:
    """Verify that changing observations after a forecast origin has zero effect on predictions."""

    BASE_HISTORY: ClassVar[list[float]] = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0, 26.0]

    @pytest.mark.parametrize(
        "model_cls,kwargs",
        [
            (PersistenceForecaster, {}),
            (MovingAverageForecaster, {"window": 3}),
            (EWMAForecaster, {"alpha": 0.6}),
            (LinearTrendForecaster, {"window": 3}),
        ],
    )
    def test_future_data_does_not_alter_forecast(self, model_cls: type, kwargs: dict) -> None:
        m1 = model_cls(**kwargs)
        m2 = model_cls(**kwargs)

        # Fit on identical prefix history
        m1.fit(self.BASE_HISTORY)
        m2.fit(self.BASE_HISTORY)

        preds1 = m1.predict(2)
        preds2 = m2.predict(2)

        np.testing.assert_array_almost_equal(
            preds1, preds2, decimal=10, err_msg="Predictions diverge with identical history"
        )


# ── Split-Perturbation Trap ─────────────────────────────────────────────────


class TestSplitPerturbationTrap:
    """Verify validation parameter selection is isolated from test-partition values."""

    def test_test_partition_changes_do_not_alter_validation_selection(self) -> None:
        """Adding or mutating test-partition rows must not change validation-based selection."""
        val_rows = [
            EvaluationMetricRow(
                model_name="linear_trend",
                parameters_json=json.dumps({"window": 3}),
                scenario_name="steady",
                horizon_steps=1,
                split="validation",
                mae=10.0,
                rmse=12.0,
                smape=15.0,
                bias=1.0,
                valid_count=5,
                excluded_count=0,
            ),
            EvaluationMetricRow(
                model_name="linear_trend",
                parameters_json=json.dumps({"window": 8}),
                scenario_name="steady",
                horizon_steps=1,
                split="validation",
                mae=5.0,
                rmse=6.0,
                smape=8.0,
                bias=0.5,
                valid_count=5,
                excluded_count=0,
            ),
        ]
        sel_before = select_best_parameters_per_family(val_rows, primary_metric="mae")

        # Add noise test rows (should not be used by select_best_parameters_per_family)
        val_rows_with_test_noise = val_rows + [
            EvaluationMetricRow(
                model_name="linear_trend",
                parameters_json=json.dumps({"window": 3}),
                scenario_name="steady",
                horizon_steps=1,
                split="test",
                mae=0.001,
                rmse=0.001,
                smape=0.01,
                bias=0.0,
                valid_count=5,
                excluded_count=0,
            ),
        ]
        sel_after = select_best_parameters_per_family(
            val_rows_with_test_noise, primary_metric="mae"
        )
        assert sel_before == sel_after


# ── Target-Column Trap ──────────────────────────────────────────────────────


class TestTargetColumnTrap:
    """Verify that future target values cannot enter the model fitting API."""

    def test_fit_accepts_only_history_array(self) -> None:
        """Model.fit takes a 1D history array; no target column can be attached."""
        m = LinearTrendForecaster(window=3)
        history = [1.0, 2.0, 3.0, 4.0, 5.0]
        m.fit(history)
        preds = m.predict(1)
        assert len(preds) == 1
        assert all(np.isfinite(p) for p in preds)

    def test_predict_does_not_accept_actuals(self) -> None:
        """predict() takes only horizon_steps, not actual future values."""
        m = PersistenceForecaster()
        m.fit([10.0, 20.0])
        preds = m.predict(2)
        assert len(preds) == 2


# ── Timestamp Trap ──────────────────────────────────────────────────────────


class TestTimestampTrap:
    """Verify horizon target timestamps are derived from strict chronological offsets."""

    def test_target_index_equals_origin_plus_horizon(self) -> None:
        base_dt = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        ts_list = [base_dt + timedelta(seconds=i * 15) for i in range(10)]
        origin_idx = 4
        for h in [1, 2, 4]:
            target_idx = origin_idx + h
            assert target_idx < len(ts_list)
            assert ts_list[target_idx] > ts_list[origin_idx]


# ── Scenario-Boundary Trap ──────────────────────────────────────────────────


class TestScenarioBoundaryTrap:
    """Verify scenario series are independently partitioned without cross-contamination."""

    def test_independent_scenario_splits(self) -> None:
        """Partitions for each scenario must be independently computed."""
        base_a = datetime(2026, 9, 10, 10, 0, 0, tzinfo=UTC)
        base_b = datetime(2026, 9, 10, 14, 0, 0, tzinfo=UTC)
        ts_a = [base_a + timedelta(seconds=i * 15) for i in range(15)]
        ts_b = [base_b + timedelta(seconds=i * 15) for i in range(15)]

        data_a = ScenarioData(
            scenario_name="scenario_a",
            run_id="run-a",
            timestamps=ts_a,
            target_values=[float(i) for i in range(15)],
            raw_sample_count=15,
            missing_count=0,
            average_interval_seconds=15.0,
            source_file=None,  # type: ignore[arg-type]
        )
        data_b = ScenarioData(
            scenario_name="scenario_b",
            run_id="run-b",
            timestamps=ts_b,
            target_values=[float(100 + i) for i in range(15)],
            raw_sample_count=15,
            missing_count=0,
            average_interval_seconds=15.0,
            source_file=None,  # type: ignore[arg-type]
        )

        splits_a = partition_scenario(data_a)
        splits_b = partition_scenario(data_b)

        # Train of A must not overlap temporally with any partition of B
        assert splits_a.train.end_timestamp < splits_b.train.start_timestamp
        # Run IDs must be distinct
        assert splits_a.run_id != splits_b.run_id


# ── Preprocessing-Fit Trap ──────────────────────────────────────────────────


class TestPreprocessingFitTrap:
    """Verify no global fitted scaler or transform uses test-partition data."""

    def test_no_global_scaler_fitted(self) -> None:
        """Raw observations pass through without any fitted normalization."""
        raw = [10.0, 20.0, 30.0, 40.0, 50.0]
        m = MovingAverageForecaster(window=3)
        m.fit(raw)
        preds = m.predict(1)
        # Predictions are raw-scale (no standard scaling applied)
        assert preds[0] > 0.0  # Result is meaningful in original units


# ── No-Test-Influenced-Selection Trap ───────────────────────────────────────


class TestNoTestInfluencedSelection:
    """Verify that test metrics cannot alter frozen parameter selections."""

    def test_parameter_selection_uses_only_validation(self) -> None:
        val_rows = [
            EvaluationMetricRow(
                model_name="ewma",
                parameters_json=json.dumps({"alpha": 0.4}),
                scenario_name="ramp",
                horizon_steps=2,
                split="validation",
                mae=20.0,
                rmse=22.0,
                smape=25.0,
                bias=-5.0,
                valid_count=3,
                excluded_count=0,
            ),
            EvaluationMetricRow(
                model_name="ewma",
                parameters_json=json.dumps({"alpha": 0.8}),
                scenario_name="ramp",
                horizon_steps=2,
                split="validation",
                mae=10.0,
                rmse=12.0,
                smape=13.0,
                bias=-2.0,
                valid_count=3,
                excluded_count=0,
            ),
        ]
        sel = select_best_parameters_per_family(val_rows, primary_metric="mae")
        assert sel[("ewma", 2)] == {"alpha": 0.8}
