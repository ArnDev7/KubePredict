from pathlib import Path
from typing import Literal

from load_testing.config import ScenarioConfig, load_scenario_config
from load_testing.scenarios.base import DeterministicLoadShape, ScenarioProfile

SCENARIOS_DIR = Path(__file__).parent

SCENARIO_NAMES = ("steady", "ramp", "periodic", "sudden_increase", "drift")
ScenarioName = Literal["steady", "ramp", "periodic", "sudden_increase", "drift"]


def get_scenario_path(name: str) -> Path:
    yaml_path = SCENARIOS_DIR / f"{name}.yaml"
    if not yaml_path.is_file():
        raise FileNotFoundError(f"Scenario YAML not found: {yaml_path}")
    return yaml_path


def load_named_scenario(
    name: str,
    allow_non_local_host: bool = False,
    host_override: str | None = None,
) -> ScenarioConfig:
    return load_scenario_config(
        get_scenario_path(name),
        allow_non_local_host=allow_non_local_host,
        host_override=host_override,
    )


__all__ = [
    "SCENARIO_NAMES",
    "DeterministicLoadShape",
    "ScenarioName",
    "ScenarioProfile",
    "get_scenario_path",
    "load_named_scenario",
]
