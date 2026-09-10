from enum import Enum


class ControllerMode(str, Enum):
    OBSERVE = "observe"
    DRY_RUN = "dry_run"
    LIVE = "live"


class ScalingDirection(str, Enum):
    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"
    NO_CHANGE = "no_change"


class DecisionReason(str, Enum):
    FORECAST_DEMAND = "forecast_demand"
    REACTIVE_FALLBACK = "reactive_fallback"
    BURST_OVERRIDE = "burst_override"
    LOW_CONFIDENCE = "low_confidence"
    WORKLOAD_DRIFT = "workload_drift"
    STALE_TELEMETRY = "stale_telemetry"
    COOLDOWN_ACTIVE = "cooldown_active"
    MINIMUM_BOUND = "minimum_bound"
    MAXIMUM_BOUND = "maximum_bound"
    NO_CHANGE_REQUIRED = "no_change_required"
    INSUFFICIENT_DATA = "insufficient_data"


class ExperimentMode(str, Enum):
    FIXED = "fixed"
    HPA = "hpa"
    PREDICTIVE = "predictive"
    GUARDED_PREDICTIVE = "guarded_predictive"
