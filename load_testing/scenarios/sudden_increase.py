from load_testing.scenarios import load_named_scenario
from load_testing.scenarios.base import DeterministicLoadShape, ScenarioProfile

config = load_named_scenario("sudden_increase")
profile = ScenarioProfile(config)


class SuddenIncreaseLoadShape(DeterministicLoadShape):
    profile = profile
