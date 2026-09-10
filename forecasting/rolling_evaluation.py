"""Rolling-origin evaluation engine for chronological time-series forecasting."""

import json
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from forecasting.base import BaseForecaster, ForecastingConfig
from forecasting.metrics import calculate_bias, calculate_mae, calculate_rmse, calculate_smape
from forecasting.preprocessing import ScenarioData, ScenarioSplits


@dataclass(frozen=True)
class PredictionRecord:
    """Individual forecast prediction evidence record."""

    scenario_name: str
    run_id: str
    model_name: str
    parameters_json: str
    horizon_steps: int
    split: str  # 'validation' or 'test'
    origin_index: int
    origin_timestamp_utc: str
    target_index: int
    target_timestamp_utc: str
    predicted_value: float
    actual_value: float
    absolute_error: float
    squared_error: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExcludedOriginRecord:
    """Record of an origin excluded from evaluation with explicit reason."""

    scenario_name: str
    run_id: str
    model_name: str
    parameters_json: str
    horizon_steps: int
    split: str
    origin_index: int
    origin_timestamp_utc: str
    target_index: int
    target_timestamp_utc: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvaluationMetricRow(BaseModel):
    """Aggregated evaluation metric record."""

    model_name: str
    parameters_json: str
    scenario_name: str
    horizon_steps: int
    split: str
    mae: float = Field(ge=0.0)
    rmse: float = Field(ge=0.0)
    smape: float = Field(ge=0.0, le=200.0)
    bias: float
    valid_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)


def evaluate_scenario_split(
    forecaster: BaseForecaster,
    data: ScenarioData,
    splits: ScenarioSplits,
    horizon: int,
    split_name: str,  # 'validation' or 'test'
    config: ForecastingConfig,
) -> tuple[list[PredictionRecord], list[ExcludedOriginRecord]]:
    """Perform rolling-origin evaluation for a given model, scenario, horizon, and split.

    Split isolation guarantee:
        - Target index target_idx = origin_idx + horizon.
        - If split_name == 'validation': target_idx must be in [val.start_index, val.end_index).
          No observations from the test partition are ever accessible or evaluated.
        - If split_name == 'test': target_idx must be in [test.start_index, test.end_index).
    """
    target_split = splits.validation if split_name == "validation" else splits.test
    params_json = json.dumps(forecaster.parameters, sort_keys=True)

    predictions: list[PredictionRecord] = []
    exclusions: list[ExcludedOriginRecord] = []

    # Iterate through each valid target index in the partition
    for target_idx in range(
        target_split.start_index, target_split.end_index, config.rolling_step_size
    ):
        origin_idx = target_idx - horizon
        if origin_idx < 0:
            # Cannot forecast from before the start of the series
            target_ts_str = data.timestamps[target_idx].isoformat()
            exclusions.append(
                ExcludedOriginRecord(
                    scenario_name=data.scenario_name,
                    run_id=data.run_id,
                    model_name=forecaster.name,
                    parameters_json=params_json,
                    horizon_steps=horizon,
                    split=split_name,
                    origin_index=origin_idx,
                    origin_timestamp_utc="",
                    target_index=target_idx,
                    target_timestamp_utc=target_ts_str,
                    reason="negative_origin_index",
                )
            )
            continue

        origin_ts_str = data.timestamps[origin_idx].isoformat()
        target_ts_str = data.timestamps[target_idx].isoformat()

        # Check target value validity
        target_actual = data.target_values[target_idx]
        if target_actual is None or not np.isfinite(target_actual):
            exclusions.append(
                ExcludedOriginRecord(
                    scenario_name=data.scenario_name,
                    run_id=data.run_id,
                    model_name=forecaster.name,
                    parameters_json=params_json,
                    horizon_steps=horizon,
                    split=split_name,
                    origin_index=origin_idx,
                    origin_timestamp_utc=origin_ts_str,
                    target_index=target_idx,
                    target_timestamp_utc=target_ts_str,
                    reason="missing_target_value",
                )
            )
            continue

        # Extract history available at origin: observations 0 ... origin_idx
        full_history = data.target_values[: origin_idx + 1]

        # Determine history window required by model
        min_required = forecaster.minimum_history_required
        if len(full_history) < min_required:
            exclusions.append(
                ExcludedOriginRecord(
                    scenario_name=data.scenario_name,
                    run_id=data.run_id,
                    model_name=forecaster.name,
                    parameters_json=params_json,
                    horizon_steps=horizon,
                    split=split_name,
                    origin_index=origin_idx,
                    origin_timestamp_utc=origin_ts_str,
                    target_index=target_idx,
                    target_timestamp_utc=target_ts_str,
                    reason=f"insufficient_history_{len(full_history)}_lt_{min_required}",
                )
            )
            continue

        # Check for missing values in the required history
        # For trailing window models (MA, LinearTrend), check the latest `window` items
        # For expanding models (Persistence, EWMA), check relevant history
        if forecaster.name in {"moving_average", "linear_trend"}:
            w = forecaster.parameters.get("window", min_required)
            history_window = full_history[-w:]
        elif forecaster.name == "persistence":
            history_window = full_history[-1:]
        else:  # ewma
            history_window = full_history

        if any(val is None or not np.isfinite(val) for val in history_window):
            exclusions.append(
                ExcludedOriginRecord(
                    scenario_name=data.scenario_name,
                    run_id=data.run_id,
                    model_name=forecaster.name,
                    parameters_json=params_json,
                    horizon_steps=horizon,
                    split=split_name,
                    origin_index=origin_idx,
                    origin_timestamp_utc=origin_ts_str,
                    target_index=target_idx,
                    target_timestamp_utc=target_ts_str,
                    reason="missing_history_value",
                )
            )
            continue

        # Convert history window to valid float array for fitting
        valid_history = [float(v) for v in history_window if v is not None]

        try:
            forecaster.fit(valid_history)
            forecasts = forecaster.predict(horizon)
            # The forecast for step h is forecasts[h-1]
            predicted_val = forecasts[horizon - 1]
        except (ValueError, RuntimeError) as ex:
            exclusions.append(
                ExcludedOriginRecord(
                    scenario_name=data.scenario_name,
                    run_id=data.run_id,
                    model_name=forecaster.name,
                    parameters_json=params_json,
                    horizon_steps=horizon,
                    split=split_name,
                    origin_index=origin_idx,
                    origin_timestamp_utc=origin_ts_str,
                    target_index=target_idx,
                    target_timestamp_utc=target_ts_str,
                    reason=f"model_error_{type(ex).__name__}",
                )
            )
            continue

        abs_err = float(abs(target_actual - predicted_val))
        sq_err = float((target_actual - predicted_val) ** 2)

        predictions.append(
            PredictionRecord(
                scenario_name=data.scenario_name,
                run_id=data.run_id,
                model_name=forecaster.name,
                parameters_json=params_json,
                horizon_steps=horizon,
                split=split_name,
                origin_index=origin_idx,
                origin_timestamp_utc=origin_ts_str,
                target_index=target_idx,
                target_timestamp_utc=target_ts_str,
                predicted_value=predicted_val,
                actual_value=target_actual,
                absolute_error=abs_err,
                squared_error=sq_err,
            )
        )

    return predictions, exclusions


def compute_metrics_table(
    predictions: list[PredictionRecord],
    exclusions: list[ExcludedOriginRecord],
) -> list[EvaluationMetricRow]:
    """Compute granular metrics table grouped by (model, parameters, scenario, horizon, split)."""
    # Group predictions by (model_name, parameters_json, scenario_name, horizon_steps, split)
    groups: dict[tuple[str, str, str, int, str], list[PredictionRecord]] = {}
    for p in predictions:
        key = (p.model_name, p.parameters_json, p.scenario_name, p.horizon_steps, p.split)
        groups.setdefault(key, []).append(p)

    # Count exclusions by same key
    excl_counts: dict[tuple[str, str, str, int, str], int] = {}
    for e in exclusions:
        key = (e.model_name, e.parameters_json, e.scenario_name, e.horizon_steps, e.split)
        excl_counts[key] = excl_counts.get(key, 0) + 1

    metric_rows: list[EvaluationMetricRow] = []
    # Sort keys for deterministic output
    all_keys = sorted(set(groups.keys()) | set(excl_counts.keys()))
    for key in all_keys:
        m_name, params_json, s_name, horizon, split = key
        preds = groups.get(key, [])
        excl_cnt = excl_counts.get(key, 0)

        if not preds:
            # All origins excluded for this group
            metric_rows.append(
                EvaluationMetricRow(
                    model_name=m_name,
                    parameters_json=params_json,
                    scenario_name=s_name,
                    horizon_steps=horizon,
                    split=split,
                    mae=float("inf"),
                    rmse=float("inf"),
                    smape=200.0,
                    bias=0.0,
                    valid_count=0,
                    excluded_count=excl_cnt,
                )
            )
            continue

        actuals = [p.actual_value for p in preds]
        forecasts = [p.predicted_value for p in preds]

        metric_rows.append(
            EvaluationMetricRow(
                model_name=m_name,
                parameters_json=params_json,
                scenario_name=s_name,
                horizon_steps=horizon,
                split=split,
                mae=calculate_mae(actuals, forecasts),
                rmse=calculate_rmse(actuals, forecasts),
                smape=calculate_smape(actuals, forecasts),
                bias=calculate_bias(actuals, forecasts),
                valid_count=len(preds),
                excluded_count=excl_cnt,
            )
        )

    return metric_rows
