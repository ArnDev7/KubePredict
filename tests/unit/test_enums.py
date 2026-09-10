from shared.enums import ControllerMode, DecisionReason, ExperimentMode, ScalingDirection


def test_controller_mode_values():
    assert ControllerMode.OBSERVE.value == "observe"
    assert ControllerMode.DRY_RUN.value == "dry_run"
    assert ControllerMode.LIVE.value == "live"


def test_scaling_direction_values():
    assert ScalingDirection.SCALE_UP.value == "scale_up"
    assert ScalingDirection.SCALE_DOWN.value == "scale_down"
    assert ScalingDirection.NO_CHANGE.value == "no_change"


def test_decision_reason_values():
    assert DecisionReason.FORECAST_DEMAND.value == "forecast_demand"
    assert DecisionReason.WORKLOAD_DRIFT.value == "workload_drift"
    assert DecisionReason.NO_CHANGE_REQUIRED.value == "no_change_required"


def test_experiment_mode_values():
    assert ExperimentMode.FIXED.value == "fixed"
    assert ExperimentMode.GUARDED_PREDICTIVE.value == "guarded_predictive"
