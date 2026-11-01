from enum import Enum


class ControllerMode(str, Enum):
    OBSERVE = "observe"
    DRY_RUN = "dry_run"
    LIVE = "live"


class ScalingDirection(str, Enum):
    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"
    NO_CHANGE = "no_change"


class PolicyMode(str, Enum):
    PREDICTIVE = "predictive"
    REACTIVE_FALLBACK = "reactive_fallback"
    RAPID_CHANGE_OVERRIDE = "rapid_change_override"
    NO_RECOMMENDATION = "no_recommendation"


class DecisionReason(str, Enum):
    FORECAST_DEMAND = "forecast_demand"
    TRUSTED_FORECAST = "trusted_forecast"
    REACTIVE_FALLBACK = "reactive_fallback"
    REACTIVE_CPU_UTILIZATION = "reactive_cpu_utilization"
    BURST_OVERRIDE = "burst_override"
    RAPID_CHANGE_DETECTED = "rapid_change_detected"
    LOW_CONFIDENCE = "low_confidence"
    WORKLOAD_DRIFT = "workload_drift"
    STALE_TELEMETRY = "stale_telemetry"
    TELEMETRY_UNAVAILABLE = "telemetry_unavailable"
    TELEMETRY_STALE = "telemetry_stale"
    INSUFFICIENT_HISTORY = "insufficient_history"
    FORECAST_UNAVAILABLE = "forecast_unavailable"
    FORECAST_UNTRUSTED = "forecast_untrusted"
    INSUFFICIENT_RESIDUAL_HISTORY = "insufficient_residual_history"
    COOLDOWN_ACTIVE = "cooldown_active"
    DOWNSCALE_STABILIZED = "downscale_stabilized"
    MINIMUM_BOUND = "minimum_bound"
    MAXIMUM_BOUND = "maximum_bound"
    SCALE_UP_STEP_LIMITED = "scale_up_step_limited"
    SCALE_DOWN_STEP_LIMITED = "scale_down_step_limited"
    NO_CHANGE_REQUIRED = "no_change_required"
    INSUFFICIENT_DATA = "insufficient_data"


class ExperimentMode(str, Enum):
    FIXED = "fixed"
    HPA = "hpa"
    PREDICTIVE = "predictive"
    GUARDED_PREDICTIVE = "guarded_predictive"


class TrustOutcome(str, Enum):
    TRUSTED = "trusted"
    TELEMETRY_UNAVAILABLE = "telemetry_unavailable"
    TELEMETRY_STALE = "telemetry_stale"
    INSUFFICIENT_HISTORY = "insufficient_history"
    FORECAST_UNAVAILABLE = "forecast_unavailable"
    FORECAST_INVALID = "forecast_invalid"
    INSUFFICIENT_RESIDUAL_HISTORY = "insufficient_residual_history"
    RESIDUAL_HISTORY_STALE = "residual_history_stale"
    ABSOLUTE_ERROR_TOO_HIGH = "absolute_error_too_high"
    RELATIVE_ERROR_TOO_HIGH = "relative_error_too_high"
    ERROR_THRESHOLD_EXCEEDED = "error_threshold_exceeded"
    GUARD_DISABLED = "guard_disabled"


class PrimaryErrorMetric(str, Enum):
    MAE = "mae"
    RMSE = "rmse"
    SMAPE = "smape"
