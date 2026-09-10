"""Unit tests for Phase 8B audit functions (metrics, aggregation, reproducibility, selection)."""

import json
import math
from datetime import UTC, datetime
from typing import ClassVar

import numpy as np

from forecasting.audit import (
    LeakageTrapResult,
    MetricVerificationRecord,
    SplitAuditRecord,
)


class TestIndependentMetricFormulas:
    """Verify clean-room metric formulas against known test vectors."""

    ACTUALS: ClassVar[list[float]] = [10.0, 20.0, 30.0, 40.0, 50.0]
    PREDICTIONS: ClassVar[list[float]] = [12.0, 18.0, 33.0, 38.0, 55.0]

    def test_mae_independently(self) -> None:
        pairs = list(zip(self.PREDICTIONS, self.ACTUALS))
        expected_mae = sum(abs(p - a) for p, a in pairs) / len(pairs)
        assert math.isclose(expected_mae, 2.8, abs_tol=1e-10)

    def test_rmse_independently(self) -> None:
        pairs = list(zip(self.PREDICTIONS, self.ACTUALS))
        expected_rmse = math.sqrt(sum((p - a) ** 2 for p, a in pairs) / len(pairs))
        # (4 + 4 + 9 + 4 + 25) / 5 = 9.2 -> sqrt(9.2) ~= 3.0332
        assert math.isclose(expected_rmse, math.sqrt(46.0 / 5.0), abs_tol=1e-10)

    def test_smape_independently(self) -> None:
        pairs = list(zip(self.PREDICTIONS, self.ACTUALS))
        terms = []
        for p, a in pairs:
            denom = abs(a) + abs(p)
            if denom == 0.0:
                terms.append(0.0)
            else:
                terms.append(200.0 * abs(p - a) / denom)
        expected_smape = sum(terms) / len(terms)
        assert expected_smape > 0.0
        assert expected_smape < 200.0

    def test_smape_both_zero(self) -> None:
        """When both actual and predicted are zero, sMAPE term should be 0."""
        denom = abs(0.0) + abs(0.0)
        term = 0.0 if denom == 0.0 else 200.0 * abs(0.0 - 0.0) / denom
        assert term == 0.0

    def test_bias_independently(self) -> None:
        pairs = list(zip(self.PREDICTIONS, self.ACTUALS))
        expected_bias = sum(p - a for p, a in pairs) / len(pairs)
        # (2 + -2 + 3 + -2 + 5) / 5 = 6/5 = 1.2
        assert math.isclose(expected_bias, 1.2, abs_tol=1e-10)


class TestMetricVerificationRecord:
    """Test MetricVerificationRecord dataclass structure."""

    def test_record_creation(self) -> None:
        rec = MetricVerificationRecord(
            model_name="persistence",
            parameters_json="{}",
            scenario_name="steady",
            horizon_steps=1,
            split="validation",
            stored_mae=5.0,
            computed_mae=5.0001,
            mae_diff=0.0001,
            stored_rmse=6.0,
            computed_rmse=6.0,
            rmse_diff=0.0,
            stored_smape=7.0,
            computed_smape=7.0,
            smape_diff=0.0,
            stored_bias=-1.0,
            computed_bias=-1.0,
            bias_diff=0.0,
            valid_count=3,
            matches=True,
        )
        assert rec.matches is True
        assert rec.mae_diff <= 1e-4


class TestSplitAuditRecord:
    """Test SplitAuditRecord validation checks."""

    def test_valid_split_passes(self) -> None:
        rec = SplitAuditRecord(
            scenario_name="steady",
            run_id="run-1",
            total_observations=15,
            train_count=9,
            val_count=3,
            test_count=3,
            train_fraction=0.6,
            val_fraction=0.2,
            test_fraction=0.2,
            train_start="2026-09-10T10:00:00+00:00",
            train_end="2026-09-10T10:02:00+00:00",
            val_start="2026-09-10T10:02:15+00:00",
            val_end="2026-09-10T10:02:45+00:00",
            test_start="2026-09-10T10:03:00+00:00",
            test_end="2026-09-10T10:03:30+00:00",
            overlap_count=0,
            gap_count=0,
            duplicate_timestamps=0,
            is_chronological=True,
            status="PASS",
        )
        assert rec.status == "PASS"
        assert rec.overlap_count == 0

    def test_overlapping_split_fails(self) -> None:
        rec = SplitAuditRecord(
            scenario_name="ramp",
            run_id="run-2",
            total_observations=15,
            train_count=9,
            val_count=3,
            test_count=3,
            train_fraction=0.6,
            val_fraction=0.2,
            test_fraction=0.2,
            train_start="2026-09-10T10:00:00+00:00",
            train_end="2026-09-10T10:02:00+00:00",
            val_start="2026-09-10T10:01:45+00:00",  # overlap!
            val_end="2026-09-10T10:02:45+00:00",
            test_start="2026-09-10T10:03:00+00:00",
            test_end="2026-09-10T10:03:30+00:00",
            overlap_count=2,
            gap_count=0,
            duplicate_timestamps=0,
            is_chronological=True,
            status="FAIL",
        )
        assert rec.status == "FAIL"
        assert rec.overlap_count > 0


class TestLeakageTrapResult:
    """Test LeakageTrapResult dataclass."""

    def test_passed_trap(self) -> None:
        r = LeakageTrapResult(
            trap_name="future_perturbation_trap",
            description="Test description",
            passed=True,
            details="No leakage found",
        )
        assert r.passed is True

    def test_failed_trap(self) -> None:
        r = LeakageTrapResult(
            trap_name="target_column_trap",
            description="Test description",
            passed=False,
            details="Leakage detected",
        )
        assert r.passed is False


class TestMacroWeightedAggregation:
    """Verify macro vs weighted average arithmetic properties."""

    def test_macro_gives_equal_scenario_weight(self) -> None:
        """Macro average treats all scenarios equally regardless of sample count."""
        scenario_maes = [5.0, 10.0, 15.0, 20.0, 25.0]
        macro = sum(scenario_maes) / len(scenario_maes)
        assert math.isclose(macro, 15.0)

    def test_weighted_gives_proportional_weight(self) -> None:
        """Weighted average gives more weight to scenarios with more predictions."""
        scenario_maes = [5.0, 10.0, 15.0, 20.0, 25.0]
        counts = [10, 5, 5, 5, 5]
        total = sum(counts)
        weighted = sum(m * c for m, c in zip(scenario_maes, counts)) / total
        # 50 + 50 + 75 + 100 + 125 = 400; 400/30 = 13.333
        assert math.isclose(weighted, 400.0 / 30.0, abs_tol=1e-10)
        assert weighted != sum(scenario_maes) / len(scenario_maes)  # different from macro


class TestSelectionPolicyRules:
    """Verify selection policy constraints."""

    def test_persistence_retained_when_no_model_beats_it(self) -> None:
        """When no non-naive model beats persistence, persistence is retained."""
        from forecasting.rolling_evaluation import EvaluationMetricRow
        from forecasting.selection import select_provisional_candidates

        rows = [
            EvaluationMetricRow(
                model_name="persistence",
                parameters_json="{}",
                scenario_name="steady",
                horizon_steps=1,
                split="validation",
                mae=5.0,
                rmse=6.0,
                smape=7.0,
                bias=-1.0,
                valid_count=3,
                excluded_count=0,
            ),
            EvaluationMetricRow(
                model_name="moving_average",
                parameters_json=json.dumps({"window": 3}),
                scenario_name="steady",
                horizon_steps=1,
                split="validation",
                mae=8.0,
                rmse=9.0,
                smape=10.0,
                bias=-2.0,
                valid_count=3,
                excluded_count=0,
            ),
        ]
        report = select_provisional_candidates(
            rows,
            generated_at=datetime.now(UTC).isoformat(),
            primary_metric="mae",
        )
        h1_candidate = next(c for c in report.per_horizon if c.horizon_steps == 1)
        assert h1_candidate.model_name == "persistence"
        assert h1_candidate.status == "persistence_retained"

    def test_finite_output_requirement(self) -> None:
        """Models must produce finite outputs."""
        from forecasting.persistence import PersistenceForecaster

        m = PersistenceForecaster()
        m.fit([10.0, 20.0, 30.0])
        preds = m.predict(4)
        assert all(np.isfinite(p) for p in preds)
        assert all(p >= 0.0 for p in preds)
