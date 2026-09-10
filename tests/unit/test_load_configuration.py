from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from load_testing.config import ScenarioConfig, StageConfig, load_scenario_config

REPO_ROOT = Path(__file__).parent.parent.parent
SCENARIOS_DIR = REPO_ROOT / "load_testing" / "scenarios"


def _make_valid_data() -> dict[str, Any]:
    return {
        "scenario_name": "valid_test",
        "description": "Valid scenario description",
        "total_duration_seconds": 60,
        "host": "http://127.0.0.1:8000",
        "endpoint": "/work",
        "random_seed": 42,
        "cpu_iterations": 50000,
        "delay_ms": 10,
        "payload_size": 20,
        "request_timeout_seconds": 5.0,
        "stages": [
            {"duration_seconds": 20, "target_users": 2, "spawn_rate_per_second": 1.0},
            {"duration_seconds": 40, "target_users": 5, "spawn_rate_per_second": 2.0},
        ],
    }


def test_valid_scenario_config() -> None:
    data = _make_valid_data()
    cfg = ScenarioConfig.model_validate(data)
    assert cfg.scenario_name == "valid_test"
    assert cfg.total_duration_seconds == 60
    assert len(cfg.stages) == 2
    cfg.validate_host_safety(allow_non_local_host=False)


def test_total_duration_mismatch() -> None:
    data = _make_valid_data()
    data["total_duration_seconds"] = 100  # stages sum to 60
    with pytest.raises(ValidationError, match="total_duration_seconds.*must equal sum"):
        ScenarioConfig.model_validate(data)


def test_zero_duration() -> None:
    data = _make_valid_data()
    data["total_duration_seconds"] = 0
    data["stages"] = [{"duration_seconds": 0, "target_users": 1, "spawn_rate_per_second": 1.0}]
    with pytest.raises(ValidationError):
        ScenarioConfig.model_validate(data)


def test_duration_over_900() -> None:
    data = _make_valid_data()
    data["total_duration_seconds"] = 901
    data["stages"] = [{"duration_seconds": 901, "target_users": 1, "spawn_rate_per_second": 1.0}]
    with pytest.raises(ValidationError, match="must be > 0 and <= 900"):
        ScenarioConfig.model_validate(data)


def test_negative_users() -> None:
    data = _make_valid_data()
    data["stages"][0]["target_users"] = -1
    with pytest.raises(ValidationError, match="between 0 and 50"):
        ScenarioConfig.model_validate(data)


def test_users_over_50() -> None:
    data = _make_valid_data()
    data["stages"][0]["target_users"] = 51
    with pytest.raises(ValidationError, match="between 0 and 50"):
        ScenarioConfig.model_validate(data)


def test_invalid_spawn_rate() -> None:
    data = _make_valid_data()
    data["stages"][0]["spawn_rate_per_second"] = 0.0
    with pytest.raises(ValidationError, match="must be > 0 and <= 10.0"):
        ScenarioConfig.model_validate(data)


def test_spawn_rate_over_10() -> None:
    data = _make_valid_data()
    data["stages"][0]["spawn_rate_per_second"] = 10.1
    with pytest.raises(ValidationError, match="must be > 0 and <= 10.0"):
        ScenarioConfig.model_validate(data)


def test_excessive_cpu_iterations() -> None:
    data = _make_valid_data()
    data["cpu_iterations"] = 400001
    with pytest.raises(ValidationError, match="between 0 and 400,000"):
        ScenarioConfig.model_validate(data)


def test_excessive_delay() -> None:
    data = _make_valid_data()
    data["delay_ms"] = 1001
    with pytest.raises(ValidationError, match="between 0 and 1,000"):
        ScenarioConfig.model_validate(data)


def test_excessive_payload() -> None:
    data = _make_valid_data()
    data["payload_size"] = 1001
    with pytest.raises(ValidationError, match="between 0 and 1,000"):
        ScenarioConfig.model_validate(data)


def test_invalid_timeout() -> None:
    data = _make_valid_data()
    data["request_timeout_seconds"] = 0.0
    with pytest.raises(ValidationError, match="must be > 0 and <= 30.0"):
        ScenarioConfig.model_validate(data)

    data["request_timeout_seconds"] = 30.5
    with pytest.raises(ValidationError, match="must be > 0 and <= 30.0"):
        ScenarioConfig.model_validate(data)


def test_boolean_passed_as_integer() -> None:
    # Test boolean rejected for random_seed
    data = _make_valid_data()
    data["random_seed"] = True
    with pytest.raises(ValidationError, match="must be an integer, not a boolean"):
        ScenarioConfig.model_validate(data)

    # Test boolean rejected for duration_seconds
    data2 = _make_valid_data()
    data2["stages"][0]["duration_seconds"] = False
    with pytest.raises(ValidationError, match="must be an integer, not a boolean"):
        ScenarioConfig.model_validate(data2)

    # Test boolean rejected for target_users
    data3 = _make_valid_data()
    data3["stages"][0]["target_users"] = True
    with pytest.raises(ValidationError, match="must be an integer, not a boolean"):
        ScenarioConfig.model_validate(data3)


def test_unknown_fields_forbidden() -> None:
    data = _make_valid_data()
    data["malicious_extra_field"] = "unexpected"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScenarioConfig.model_validate(data)

    stage_data = data["stages"][0]
    stage_data["extra_stage_key"] = 123
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        StageConfig.model_validate(stage_data)


def test_external_host_rejected_by_default() -> None:
    data = _make_valid_data()
    data["host"] = "http://example.com"
    cfg = ScenarioConfig.model_validate(data)
    with pytest.raises(ValueError, match="External host.*rejected by default"):
        cfg.validate_host_safety(allow_non_local_host=False)


def test_explicit_non_local_host_opt_in() -> None:
    data = _make_valid_data()
    data["host"] = "http://my-k8s-dev-cluster.internal:8080"
    cfg = ScenarioConfig.model_validate(data)
    # Allowed when opt-in is True
    cfg.validate_host_safety(allow_non_local_host=True)


def test_wildcard_host_strictly_prohibited() -> None:
    data = _make_valid_data()
    data["host"] = "http://0.0.0.0:8000"
    cfg = ScenarioConfig.model_validate(data)
    with pytest.raises(ValueError, match="Wildcard host.*prohibited"):
        cfg.validate_host_safety(allow_non_local_host=True)


def test_invalid_endpoint() -> None:
    data = _make_valid_data()
    data["endpoint"] = "/metrics"
    with pytest.raises(ValidationError, match="endpoint must equal '/work'"):
        ScenarioConfig.model_validate(data)


def test_blank_names() -> None:
    data = _make_valid_data()
    data["scenario_name"] = "   "
    with pytest.raises(ValidationError, match="scenario_name must be non-empty"):
        ScenarioConfig.model_validate(data)

    data2 = _make_valid_data()
    data2["description"] = ""
    with pytest.raises(ValidationError, match="description must be non-empty"):
        ScenarioConfig.model_validate(data2)


def test_committed_scenario_files_valid() -> None:
    committed_names = ["steady", "ramp", "periodic", "sudden_increase", "drift"]
    for name in committed_names:
        yaml_path = SCENARIOS_DIR / f"{name}.yaml"
        assert yaml_path.is_file(), f"Expected scenario YAML not found: {yaml_path}"
        cfg = load_scenario_config(yaml_path)
        assert cfg.scenario_name == name
        assert cfg.total_duration_seconds >= 120
        assert cfg.total_duration_seconds <= 360
