import os
from unittest import mock

import pytest
from pydantic import ValidationError

from config.settings import Settings
from shared.enums import ControllerMode


def test_settings_safe_defaults():
    settings = Settings()
    assert settings.app_host == "0.0.0.0"
    assert settings.app_port == 8000
    assert settings.controller_mode == ControllerMode.OBSERVE
    assert settings.enable_live_scaling is False


def test_settings_replica_bounds():
    with pytest.raises(ValidationError, match="minimum_replicas must be <= maximum_replicas"):
        Settings(minimum_replicas=10, maximum_replicas=5)


def test_settings_live_scaling_protection():
    with pytest.raises(
        ValidationError, match="enable_live_scaling=true is invalid unless controller_mode=live"
    ):
        Settings(controller_mode=ControllerMode.OBSERVE, enable_live_scaling=True)


def test_settings_live_mode_with_safety_switch_false():
    settings = Settings(controller_mode=ControllerMode.LIVE, enable_live_scaling=False)
    assert settings.controller_mode == ControllerMode.LIVE
    assert settings.enable_live_scaling is False


def test_settings_valid_explicit_live_mode():
    settings = Settings(controller_mode=ControllerMode.LIVE, enable_live_scaling=True)
    assert settings.controller_mode == ControllerMode.LIVE
    assert settings.enable_live_scaling is True


def test_settings_invalid_port():
    with pytest.raises(ValidationError):
        Settings(app_port=999999)


def test_settings_workload_safety_bounds():
    with pytest.raises(ValidationError):
        Settings(max_cpu_iterations=-1)
    with pytest.raises(ValidationError):
        Settings(max_delay_ms=20000)


@mock.patch.dict(
    os.environ,
    {"APP_PORT": "9090", "ENABLE_LIVE_SCALING": "true", "CONTROLLER_MODE": "live"},
    clear=True,
)
def test_settings_env_parsing():
    settings = Settings()
    assert settings.app_port == 9090
    assert settings.enable_live_scaling is True
    assert settings.controller_mode == ControllerMode.LIVE
