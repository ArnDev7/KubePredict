"""Forecast Trust Guard policy evaluation engine (Phase 10A).

Evaluates telemetry, forecast validity, and recent matched residual window
metrics against configured error thresholds to make deterministic, reproducible,
auditable trust decisions before forecast scaling recommendations are considered.
"""

import math
from collections.abc import Sequence
from datetime import datetime

from controller.forecast_store import PendingForecast
from controller.residual_window import ResidualWindowResult, build_residual_window
from controller.telemetry import TelemetryResult
from controller.trust_config import TrustGuardConfig
from controller.trust_metrics import calculate_trust_metrics
from shared.enums import TrustOutcome
from shared.models import ForecastTrustDecision
from shared.validation import require_timezone_aware


def evaluate_forecast_trust(
    current_time: datetime,
    config: TrustGuardConfig,
    telemetry: TelemetryResult | None,
    forecast: PendingForecast | None,
    candidate_residuals: Sequence[PendingForecast],
) -> ForecastTrustDecision:
    """Evaluate deterministic trust in the current forecast according to Phase 10A policy.

    Parameters
    ----------
    current_time : datetime
        Current UTC evaluation timestamp.
    config : TrustGuardConfig
        Validated TrustGuardConfig parameters and error thresholds.
    telemetry : TelemetryResult | None
        Recent telemetry observation results, or None if query failed.
    forecast : PendingForecast | None
        Current forecast produced by frozen ForecastingService, or None.
    candidate_residuals : Sequence[PendingForecast]
        All historical matched forecast records available in the forecast store.

    Returns
    -------
    ForecastTrustDecision
        Deterministic, validated trust decision record with full audit evidence.
    """
    eval_utc = require_timezone_aware(current_time)
    warnings: list[str] = []
    gates: dict[str, bool] = {
        "guard_enabled": False,
        "telemetry_available": False,
        "telemetry_fresh": False,
        "history_sufficient": False,
        "forecast_available": False,
        "forecast_valid": False,
        "residual_count_sufficient": False,
        "residual_fresh": False,
        "absolute_error_passed": False,
        "relative_error_passed": False,
    }

    # Extract forecast attributes if available
    forecast_id: str | None = forecast.forecast_id if forecast else None
    model_name: str = forecast.model_name if forecast else "unknown"
    forecast_gen_ts: datetime | None = forecast.generated_at if forecast else None
    forecast_target_ts: datetime | None = forecast.target_timestamp if forecast else None
    if forecast and forecast.warnings:
        warnings.extend(forecast.warnings)

    # Extract telemetry attributes if available
    telemetry_latest_ts: datetime | None = None
    telemetry_age: float | None = None
    telemetry_fresh: bool = False
    history_obs: int = 0
    if telemetry:
        history_obs = telemetry.observation_count
        telemetry_age = round(telemetry.latest_age_seconds, 2)
        telemetry_fresh = bool(telemetry.is_fresh)
        if telemetry.timestamps:
            telemetry_latest_ts = telemetry.timestamps[-1]
        if telemetry.warnings:
            warnings.extend(telemetry.warnings)

    min_history_req = max(config.minimum_history_observations, 1)

    # Default residual window tracking
    win: ResidualWindowResult | None = None
    matched_count = 0
    win_start: datetime | None = None
    win_end: datetime | None = None
    recent_mae: float | None = None
    recent_rmse: float | None = None
    recent_smape: float | None = None
    recent_bias: float | None = None

    outcome: TrustOutcome
    trusted: bool = False

    # Deterministic Short-Circuit Evaluation Order:
    # 1. Guard enabled?
    if not config.enabled:
        outcome = TrustOutcome.GUARD_DISABLED
        warnings.append("Forecast trust guard is disabled by configuration")
    else:
        gates["guard_enabled"] = True

        # 2. Telemetry available?
        if telemetry is None or telemetry.status in ("unavailable", "malformed"):
            outcome = TrustOutcome.TELEMETRY_UNAVAILABLE
            warnings.append(
                f"Telemetry unavailable (status: {telemetry.status if telemetry else 'None'})"
            )
        else:
            gates["telemetry_available"] = True

            # 3. Telemetry fresh?
            if config.require_fresh_telemetry and (
                not telemetry.is_fresh or telemetry.status == "stale"
            ):
                outcome = TrustOutcome.TELEMETRY_STALE
                warnings.append(f"Telemetry stale: age={telemetry_age}s, status={telemetry.status}")
            else:
                gates["telemetry_fresh"] = True

                # 4. Sufficient model history?
                if telemetry.observation_count < min_history_req:
                    outcome = TrustOutcome.INSUFFICIENT_HISTORY
                    warnings.append(
                        f"Insufficient observation history ({telemetry.observation_count} < {min_history_req})"
                    )
                else:
                    gates["history_sufficient"] = True

                    # 5. Forecast available?
                    if forecast is None:
                        outcome = TrustOutcome.FORECAST_UNAVAILABLE
                        warnings.append("No forecast output available for evaluation")
                    else:
                        gates["forecast_available"] = True

                        # 6. Forecast numerically valid?
                        pred_rate = forecast.predicted_request_rate_per_second
                        if config.require_valid_forecast and (
                            pred_rate < 0.0 or not math.isfinite(pred_rate)
                        ):
                            outcome = TrustOutcome.FORECAST_INVALID
                            warnings.append(
                                f"Forecast output {pred_rate} is invalid (negative or non-finite)"
                            )
                        else:
                            gates["forecast_valid"] = True

                            # 7. Residual window construction
                            win = build_residual_window(
                                matched_records=candidate_residuals,
                                evaluation_time=eval_utc,
                                evidence_window_seconds=config.evidence_window_seconds,
                                residual_max_age_seconds=config.residual_max_age_seconds,
                                minimum_matched_residuals=config.minimum_matched_residuals,
                            )
                            win_start = win.window_start
                            win_end = win.window_end
                            matched_count = len(win.eligible_records)

                            # 8. Sufficient matched residual count?
                            if matched_count < config.minimum_matched_residuals:
                                outcome = TrustOutcome.INSUFFICIENT_RESIDUAL_HISTORY
                                warnings.append(
                                    f"Insufficient matched residuals ({matched_count} < {config.minimum_matched_residuals})"
                                )
                            else:
                                gates["residual_count_sufficient"] = True

                                # 9. Residual evidence fresh?
                                if (
                                    win.latest_residual_age_seconds is None
                                    or win.latest_residual_age_seconds
                                    > config.residual_max_age_seconds
                                ):
                                    outcome = TrustOutcome.RESIDUAL_HISTORY_STALE
                                    warnings.append(
                                        f"Latest matched residual is stale (age={win.latest_residual_age_seconds}s > max={config.residual_max_age_seconds}s)"
                                    )
                                else:
                                    gates["residual_fresh"] = True

                                    # 10. Calculate recent error metrics
                                    metrics = calculate_trust_metrics(
                                        records=win.eligible_records,
                                        current_time=eval_utc,
                                    )
                                    recent_mae = metrics.mae
                                    recent_rmse = metrics.rmse
                                    recent_smape = metrics.smape
                                    recent_bias = metrics.bias

                                    # 11 & 12. Evaluate error thresholds
                                    mae_exceeded = recent_mae > config.absolute_error_threshold
                                    smape_exceeded = recent_smape > config.relative_error_threshold

                                    if not mae_exceeded:
                                        gates["absolute_error_passed"] = True
                                    if not smape_exceeded:
                                        gates["relative_error_passed"] = True

                                    if mae_exceeded and smape_exceeded:
                                        outcome = TrustOutcome.ERROR_THRESHOLD_EXCEEDED
                                        warnings.append(
                                            f"Both error thresholds exceeded: MAE={recent_mae} > {config.absolute_error_threshold}, sMAPE={recent_smape}% > {config.relative_error_threshold}%"
                                        )
                                    elif mae_exceeded:
                                        outcome = TrustOutcome.ABSOLUTE_ERROR_TOO_HIGH
                                        warnings.append(
                                            f"Absolute error too high: MAE={recent_mae} > {config.absolute_error_threshold}"
                                        )
                                    elif smape_exceeded:
                                        outcome = TrustOutcome.RELATIVE_ERROR_TOO_HIGH
                                        warnings.append(
                                            f"Relative error too high: sMAPE={recent_smape}% > {config.relative_error_threshold}%"
                                        )
                                    else:
                                        # 13. All gates passed
                                        outcome = TrustOutcome.TRUSTED
                                        trusted = True

    # Assemble reproducible audit details
    details = {
        "gates": gates,
        "threshold_source": config.threshold_source,
        "threshold_evidence_reference": config.threshold_evidence_reference,
        "primary_error_metric": config.primary_error_metric.value,
        "status": config.status,
    }
    if win is not None:
        details["residual_window"] = {
            "excluded_too_old": win.excluded_too_old_count,
            "excluded_future": win.excluded_future_count,
            "excluded_invalid": win.excluded_invalid_count,
            "latest_residual_age_seconds": win.latest_residual_age_seconds,
        }

    return ForecastTrustDecision(
        evaluated_at=eval_utc,
        trusted=trusted,
        outcome=outcome,
        forecast_id=forecast_id,
        model_name=model_name,
        forecast_generated_at=forecast_gen_ts,
        forecast_target_timestamp=forecast_target_ts,
        telemetry_latest_timestamp=telemetry_latest_ts,
        telemetry_age_seconds=telemetry_age,
        telemetry_fresh=telemetry_fresh,
        history_observations=history_obs,
        minimum_history_required=min_history_req,
        matched_residual_count=matched_count,
        minimum_matched_residuals=config.minimum_matched_residuals,
        residual_window_start=win_start,
        residual_window_end=win_end,
        recent_mae=recent_mae,
        recent_rmse=recent_rmse,
        recent_smape=recent_smape,
        recent_bias=recent_bias,
        absolute_error_threshold=config.absolute_error_threshold,
        relative_error_threshold=config.relative_error_threshold,
        evidence_window_seconds=config.evidence_window_seconds,
        warnings=warnings,
        details=details,
    )
