from pathlib import Path

import pytest

from load_testing.scenarios import load_named_scenario
from load_testing.scenarios.base import ScenarioProfile

REPO_ROOT = Path(__file__).parent.parent.parent
SCENARIOS_DIR = REPO_ROOT / "load_testing" / "scenarios"


def test_deterministic_stage_lookup_and_boundaries() -> None:
    cfg = load_named_scenario("steady")
    profile = ScenarioProfile(cfg)

    # Stage 1: 0s - 30s (users: 4, spawn: 2.0)
    assert profile.get_stage_at_time(0.0) == (4, 2.0)
    assert profile.get_stage_at_time(15.5) == (4, 2.0)
    assert profile.get_stage_at_time(29.99) == (4, 2.0)

    # Exact boundary at 30s -> Stage 2: 30s - 150s (users: 12, spawn: 4.0)
    assert profile.get_stage_at_time(30.0) == (12, 4.0)
    assert profile.get_stage_at_time(100.0) == (12, 4.0)
    assert profile.get_stage_at_time(149.99) == (12, 4.0)

    # Cooldown Stage 3: 150s - 180s (users: 0, spawn: 4.0)
    assert profile.get_stage_at_time(150.0) == (0, 4.0)
    assert profile.get_stage_at_time(179.99) == (0, 4.0)

    # Test expiration: >= 180s -> None
    assert profile.get_stage_at_time(180.0) is None
    assert profile.get_stage_at_time(190.0) is None


def test_negative_elapsed_time_raises() -> None:
    cfg = load_named_scenario("steady")
    profile = ScenarioProfile(cfg)
    with pytest.raises(ValueError, match="Elapsed time cannot be negative"):
        profile.get_stage_at_time(-0.1)


def test_ramp_scenario_increasing_stages() -> None:
    cfg = load_named_scenario("ramp")
    profile = ScenarioProfile(cfg)

    # Warmup (30s) -> 4 users
    assert profile.get_stage_at_time(0.0) == (4, 2.0)
    # Ramp 1 (30s - 90s) -> 8 users
    assert profile.get_stage_at_time(30.0) == (8, 2.0)
    # Ramp 2 (90s - 150s) -> 12 users
    assert profile.get_stage_at_time(90.0) == (12, 2.0)
    # Peak (150s - 210s) -> 16 users
    assert profile.get_stage_at_time(150.0) == (16, 2.0)
    # Cooldown (210s - 240s) -> 0 users
    assert profile.get_stage_at_time(210.0) == (0, 4.0)
    # Expiry
    assert profile.get_stage_at_time(240.0) is None


def test_periodic_scenario_cyclical_repetition() -> None:
    cfg = load_named_scenario("periodic")
    profile = ScenarioProfile(cfg)

    # Warmup (0-30s): 4 users
    assert profile.get_stage_at_time(10.0) == (4, 2.0)

    # Cycle 1 High (30-75s): 14 users
    assert profile.get_stage_at_time(50.0) == (14, 4.0)
    # Cycle 1 Low (75-120s): 6 users
    assert profile.get_stage_at_time(100.0) == (6, 4.0)

    # Cycle 2 High (120-165s): 14 users
    assert profile.get_stage_at_time(140.0) == (14, 4.0)
    # Cycle 2 Low (165-210s): 6 users
    assert profile.get_stage_at_time(180.0) == (6, 4.0)

    # Cooldown (210-240s): 0 users
    assert profile.get_stage_at_time(220.0) == (0, 4.0)
    assert profile.get_stage_at_time(240.0) is None


def test_sudden_increase_abrupt_step() -> None:
    cfg = load_named_scenario("sudden_increase")
    profile = ScenarioProfile(cfg)

    # Baseline (0-60s): 4 users
    assert profile.get_stage_at_time(59.9) == (4, 2.0)
    # Abrupt jump at 60s: 18 users
    assert profile.get_stage_at_time(60.0) == (18, 5.0)
    assert profile.get_stage_at_time(149.9) == (18, 5.0)
    # Cooldown (150-180s): 0 users
    assert profile.get_stage_at_time(150.0) == (0, 5.0)
    assert profile.get_stage_at_time(180.0) is None


def test_drift_structural_change() -> None:
    cfg = load_named_scenario("drift")
    profile = ScenarioProfile(cfg)

    # Pattern 1 Low (30-75s): 6 users
    assert profile.get_stage_at_time(50.0) == (6, 2.0)
    # Pattern 1 High (75-120s): 10 users
    assert profile.get_stage_at_time(100.0) == (10, 4.0)
    # Drift stage (120-210s): 18 users (significantly higher than previous high)
    assert profile.get_stage_at_time(150.0) == (18, 4.0)
    # Cooldown (210-240s): 0 users
    assert profile.get_stage_at_time(220.0) == (0, 4.0)
    assert profile.get_stage_at_time(240.0) is None


def test_deterministic_repeatability() -> None:
    cfg = load_named_scenario("steady")
    profile1 = ScenarioProfile(cfg)
    profile2 = ScenarioProfile(cfg)

    test_points = [0.0, 15.0, 30.0, 75.0, 150.0, 179.9, 180.0]
    for pt in test_points:
        assert profile1.get_stage_at_time(pt) == profile2.get_stage_at_time(pt)
