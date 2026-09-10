"""Model ranking, parameter selection, baseline comparison, and provisional candidate selection."""

import json
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from forecasting.rolling_evaluation import EvaluationMetricRow


class ModelRankingEntry(BaseModel):
    """Validation ranking entry for a specific model configuration and horizon."""

    model_name: str
    parameters_json: str
    horizon_steps: int
    macro_mae: float
    macro_rmse: float
    macro_smape: float
    macro_bias: float
    weighted_mae: float
    weighted_rmse: float
    weighted_smape: float
    weighted_bias: float
    total_valid_predictions: int
    total_excluded_predictions: int
    rank: int = Field(ge=1)


class HorizonCandidate(BaseModel):
    """Provisional candidate selection for a single forecast horizon."""

    horizon_steps: int
    model_name: str
    parameters: dict[str, Any]
    validation_macro_mae: float
    validation_macro_rmse: float
    validation_macro_smape: float
    persistence_macro_mae: float
    mae_diff_vs_persistence: float  # candidate_mae - persistence_mae (negative is better)
    status: str  # "provisional_candidate" or "persistence_retained"
    selection_reason: str
    scenario_weaknesses: list[str] = Field(default_factory=list)
    total_valid_predictions: int
    total_excluded_predictions: int


class ProvisionalSelectionReport(BaseModel):
    """Overall Phase 8A provisional selection report across all horizons."""

    generated_at: str
    primary_metric: str
    tolerance: float
    per_horizon: list[HorizonCandidate]
    overall_candidate: HorizonCandidate
    rankings: list[ModelRankingEntry]
    summary_notes: list[str] = Field(default_factory=list)


def aggregate_macro_weighted_metrics(
    rows: list[EvaluationMetricRow],
) -> dict[tuple[str, str, int], dict[str, float]]:
    """Calculate macro and weighted metrics grouped by (model_name, parameters_json, horizon_steps)."""
    groups: dict[tuple[str, str, int], list[EvaluationMetricRow]] = {}
    for r in rows:
        key = (r.model_name, r.parameters_json, r.horizon_steps)
        groups.setdefault(key, []).append(r)

    summary: dict[tuple[str, str, int], dict[str, float]] = {}
    for key, items in groups.items():
        # Filter items with valid predictions
        valid_items = [it for it in items if it.valid_count > 0]
        total_valid = sum(it.valid_count for it in items)
        total_excl = sum(it.excluded_count for it in items)

        if not valid_items or total_valid == 0:
            summary[key] = {
                "macro_mae": float("inf"),
                "macro_rmse": float("inf"),
                "macro_smape": 200.0,
                "macro_bias": 0.0,
                "weighted_mae": float("inf"),
                "weighted_rmse": float("inf"),
                "weighted_smape": 200.0,
                "weighted_bias": 0.0,
                "total_valid": 0,
                "total_excluded": total_excl,
            }
            continue

        macro_mae = float(np.mean([it.mae for it in valid_items]))
        macro_rmse = float(np.mean([it.rmse for it in valid_items]))
        macro_smape = float(np.mean([it.smape for it in valid_items]))
        macro_bias = float(np.mean([it.bias for it in valid_items]))

        w_mae = float(sum(it.mae * it.valid_count for it in valid_items) / total_valid)
        w_rmse = float(sum(it.rmse * it.valid_count for it in valid_items) / total_valid)
        w_smape = float(sum(it.smape * it.valid_count for it in valid_items) / total_valid)
        w_bias = float(sum(it.bias * it.valid_count for it in valid_items) / total_valid)

        summary[key] = {
            "macro_mae": macro_mae,
            "macro_rmse": macro_rmse,
            "macro_smape": macro_smape,
            "macro_bias": macro_bias,
            "weighted_mae": w_mae,
            "weighted_rmse": w_rmse,
            "weighted_smape": w_smape,
            "weighted_bias": w_bias,
            "total_valid": total_valid,
            "total_excluded": total_excl,
        }

    return summary


def rank_validation_configurations(
    validation_rows: list[EvaluationMetricRow],
    primary_metric: str = "mae",
) -> list[ModelRankingEntry]:
    """Rank all model configurations for each horizon based on validation metrics."""
    agg = aggregate_macro_weighted_metrics(validation_rows)

    # Group by horizon
    by_horizon: dict[int, list[tuple[str, str, dict[str, float]]]] = {}
    for (m_name, p_json, horizon), stats in agg.items():
        by_horizon.setdefault(horizon, []).append((m_name, p_json, stats))

    ranking_entries: list[ModelRankingEntry] = []

    for horizon in sorted(by_horizon.keys()):
        configs = by_horizon[horizon]

        # Sorting key: primary metric (macro), then secondary metrics (rmse, smape), then model complexity
        def sort_key(item: tuple[str, str, dict[str, float]]) -> tuple[float, float, float, int]:
            m_name, _p_json, stats = item
            macro_prim = stats.get(f"macro_{primary_metric}", float("inf"))
            macro_rmse = stats.get("macro_rmse", float("inf"))
            macro_smape = stats.get("macro_smape", float("inf"))
            # Tie breaker: persistence (0) < moving_avg (1) < ewma (2) < linear_trend (3)
            complexity_order = {"persistence": 0, "moving_average": 1, "ewma": 2, "linear_trend": 3}
            comp = complexity_order.get(m_name, 99)
            return (macro_prim, macro_rmse, macro_smape, comp)

        configs.sort(key=sort_key)

        for rank, (m_name, p_json, stats) in enumerate(configs, start=1):
            ranking_entries.append(
                ModelRankingEntry(
                    model_name=m_name,
                    parameters_json=p_json,
                    horizon_steps=horizon,
                    macro_mae=stats["macro_mae"],
                    macro_rmse=stats["macro_rmse"],
                    macro_smape=stats["macro_smape"],
                    macro_bias=stats["macro_bias"],
                    weighted_mae=stats["weighted_mae"],
                    weighted_rmse=stats["weighted_rmse"],
                    weighted_smape=stats["weighted_smape"],
                    weighted_bias=stats["weighted_bias"],
                    total_valid_predictions=int(stats["total_valid"]),
                    total_excluded_predictions=int(stats["total_excluded"]),
                    rank=rank,
                )
            )

    return ranking_entries


def select_best_parameters_per_family(
    validation_rows: list[EvaluationMetricRow],
    primary_metric: str = "mae",
) -> dict[tuple[str, int], dict[str, Any]]:
    """Select and freeze the best parameters for each model family and horizon on validation data.

    Returns mapping of (model_family, horizon) -> parameters_dict.
    """
    rankings = rank_validation_configurations(validation_rows, primary_metric=primary_metric)
    selected: dict[tuple[str, int], dict[str, Any]] = {}

    for r in rankings:
        key = (r.model_name, r.horizon_steps)
        if key not in selected:
            # First encountered for this family and horizon is the top-ranked configuration
            selected[key] = json.loads(r.parameters_json)

    return selected


def identify_scenario_weaknesses(
    validation_rows: list[EvaluationMetricRow],
    model_name: str,
    parameters_json: str,
    horizon: int,
    persistence_rows: list[EvaluationMetricRow],
) -> list[str]:
    """Identify scenarios where the candidate model performs noticeably worse than persistence."""
    weaknesses: list[str] = []
    # Build lookup of persistence MAE per scenario
    p_lookup: dict[str, float] = {}
    for pr in persistence_rows:
        if pr.horizon_steps == horizon:
            p_lookup[pr.scenario_name] = pr.mae

    for r in validation_rows:
        if (
            r.model_name == model_name
            and r.parameters_json == parameters_json
            and r.horizon_steps == horizon
        ):
            p_mae = p_lookup.get(r.scenario_name)
            if p_mae is not None and np.isfinite(p_mae):
                if r.mae > p_mae + 0.5:
                    weaknesses.append(
                        f"Underperformed persistence on scenario '{r.scenario_name}' "
                        f"(MAE {r.mae:.3f} vs persistence {p_mae:.3f})"
                    )
                elif r.valid_count == 0 and r.excluded_count > 0:
                    weaknesses.append(f"Zero valid predictions on scenario '{r.scenario_name}'")

    return weaknesses


def select_provisional_candidates(
    validation_rows: list[EvaluationMetricRow],
    generated_at: str,
    primary_metric: str = "mae",
    tolerance: float = 1e-4,
) -> ProvisionalSelectionReport:
    """Select provisional candidate models for each horizon and overall using the Baseline Comparison Rule."""
    rankings = rank_validation_configurations(validation_rows, primary_metric=primary_metric)

    # Extract persistence rows
    persistence_rows = [r for r in validation_rows if r.model_name == "persistence"]

    # Group rankings by horizon
    by_horizon: dict[int, list[ModelRankingEntry]] = {}
    for r in rankings:
        by_horizon.setdefault(r.horizon_steps, []).append(r)

    per_horizon_candidates: list[HorizonCandidate] = []

    for horizon in sorted(by_horizon.keys()):
        horizon_ranks = by_horizon[horizon]

        # Find persistence ranking entry for this horizon
        persistence_entry = next((r for r in horizon_ranks if r.model_name == "persistence"), None)
        if persistence_entry is None:
            raise RuntimeError(
                f"Persistence baseline missing from validation rankings for horizon {horizon}"
            )

        p_macro_mae = persistence_entry.macro_mae

        # Check candidate models in ranking order
        candidate_selected = False
        selected_candidate: HorizonCandidate | None = None

        for r in horizon_ranks:
            if r.model_name == "persistence":
                continue

            # Candidate must have sufficient valid predictions
            if r.total_valid_predictions == 0:
                continue

            # Baseline Comparison Rule: MAE_candidate <= MAE_persistence + tolerance
            diff = r.macro_mae - p_macro_mae
            if diff <= tolerance:
                params = json.loads(r.parameters_json)
                weaknesses = identify_scenario_weaknesses(
                    validation_rows, r.model_name, r.parameters_json, horizon, persistence_rows
                )
                selected_candidate = HorizonCandidate(
                    horizon_steps=horizon,
                    model_name=r.model_name,
                    parameters=params,
                    validation_macro_mae=r.macro_mae,
                    validation_macro_rmse=r.macro_rmse,
                    validation_macro_smape=r.macro_smape,
                    persistence_macro_mae=p_macro_mae,
                    mae_diff_vs_persistence=diff,
                    status="provisional_candidate",
                    selection_reason=(
                        f"Outperformed or matched persistence on validation {primary_metric.upper()} "
                        f"({r.macro_mae:.4f} vs {p_macro_mae:.4f}, diff={diff:+.4f}) "
                        f"with {r.total_valid_predictions} valid predictions."
                    ),
                    scenario_weaknesses=weaknesses,
                    total_valid_predictions=r.total_valid_predictions,
                    total_excluded_predictions=r.total_excluded_predictions,
                )
                candidate_selected = True
                break

        if not candidate_selected:
            # Retain persistence
            selected_candidate = HorizonCandidate(
                horizon_steps=horizon,
                model_name="persistence",
                parameters={},
                validation_macro_mae=p_macro_mae,
                validation_macro_rmse=persistence_entry.macro_rmse,
                validation_macro_smape=persistence_entry.macro_smape,
                persistence_macro_mae=p_macro_mae,
                mae_diff_vs_persistence=0.0,
                status="persistence_retained",
                selection_reason=(
                    f"No non-naive model outperformed persistence on validation {primary_metric.upper()} "
                    f"(macro MAE={p_macro_mae:.4f}) within tolerance {tolerance}. "
                    "Persistence baseline retained in accordance with Baseline Comparison Rule."
                ),
                scenario_weaknesses=[],
                total_valid_predictions=persistence_entry.total_valid_predictions,
                total_excluded_predictions=persistence_entry.total_excluded_predictions,
            )

        if selected_candidate is not None:
            per_horizon_candidates.append(selected_candidate)

    # Overall candidate selection: choose the model configuration with lowest average validation macro MAE
    # across horizons, or the dominant candidate
    overall = min(
        per_horizon_candidates,
        key=lambda c: (0 if c.status == "provisional_candidate" else 1, c.validation_macro_mae),
    )

    notes = [
        "All parameters selected using validation split only.",
        "Test partition strictly held out and evaluated once with frozen parameters.",
        "Persistence baseline evaluated across all horizons and scenarios.",
        "Candidate status is provisional for Phase 8B offline review and does not imply production readiness.",
    ]

    return ProvisionalSelectionReport(
        generated_at=generated_at,
        primary_metric=primary_metric,
        tolerance=tolerance,
        per_horizon=per_horizon_candidates,
        overall_candidate=overall,
        rankings=rankings,
        summary_notes=notes,
    )
