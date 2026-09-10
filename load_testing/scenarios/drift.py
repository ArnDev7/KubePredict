from load_testing.scenarios import load_named_scenario
from load_testing.scenarios.base import DeterministicLoadShape, ScenarioProfile

config = load_named_scenario("drift")
profile = ScenarioProfile(config)


class DriftLoadShape(DeterministicLoadShape):
    profile = profile
