from locust import LoadTestShape

from load_testing.config import ScenarioConfig


class ScenarioProfile:
    """Encapsulates a validated ScenarioConfig and provides deterministic stage lookup."""

    def __init__(self, config: ScenarioConfig):
        self.config = config

    def get_stage_at_time(self, elapsed_seconds: float) -> tuple[int, float] | None:
        """
        Map elapsed time in seconds to (target_users, spawn_rate_per_second).
        Returns None when the test duration has elapsed or expired.
        """
        if elapsed_seconds < 0:
            raise ValueError(f"Elapsed time cannot be negative: {elapsed_seconds}")

        if elapsed_seconds >= self.config.total_duration_seconds:
            return None

        cumulative_time = 0
        for stage in self.config.stages:
            start_time = cumulative_time
            end_time = cumulative_time + stage.duration_seconds
            if start_time <= elapsed_seconds < end_time:
                return (stage.target_users, stage.spawn_rate_per_second)
            cumulative_time = end_time

        return None

    def get_stage_index_at_time(self, elapsed_seconds: float) -> int:
        """Map elapsed time in seconds to 1-based stage index."""
        if elapsed_seconds < 0:
            return 1

        cumulative_time = 0
        for idx, stage in enumerate(self.config.stages, start=1):
            start_time = cumulative_time
            end_time = cumulative_time + stage.duration_seconds
            if start_time <= elapsed_seconds < end_time:
                return idx
            cumulative_time = end_time

        return len(self.config.stages)


class DeterministicLoadShape(LoadTestShape):
    """Locust LoadTestShape driven deterministically by a ScenarioProfile."""

    profile: ScenarioProfile | None = None

    def tick(self) -> tuple[int, float] | None:
        prof = self.profile
        if prof is None:
            import os
            from pathlib import Path

            from load_testing.config import load_scenario_config
            from load_testing.scenarios import get_scenario_path

            env_config = os.environ.get("KUBEPREDICT_SCENARIO_CONFIG")
            if env_config:
                allow_non_local = (
                    os.environ.get("KUBEPREDICT_ALLOW_NON_LOCAL_HOST", "false").lower() == "true"
                )
                cfg = load_scenario_config(Path(env_config), allow_non_local_host=allow_non_local)
            else:
                cfg = load_scenario_config(get_scenario_path("steady"))
            prof = ScenarioProfile(cfg)
            self.profile = prof

        run_time = self.get_run_time()
        return prof.get_stage_at_time(run_time)
