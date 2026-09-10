import os
import uuid
from pathlib import Path

from locust import HttpUser, between, task
from locust.exception import StopUser

from load_testing.config import ScenarioConfig, load_scenario_config
from load_testing.scenarios import get_scenario_path
from load_testing.scenarios.base import DeterministicLoadShape, ScenarioProfile


def _resolve_active_config() -> ScenarioConfig:
    env_config_path = os.environ.get("KUBEPREDICT_SCENARIO_CONFIG")
    if env_config_path:
        path = Path(env_config_path)
        allow_non_local = (
            os.environ.get("KUBEPREDICT_ALLOW_NON_LOCAL_HOST", "false").lower() == "true"
        )
        return load_scenario_config(path, allow_non_local_host=allow_non_local)
    # Default fallback to steady scenario
    return load_scenario_config(get_scenario_path("steady"))


_ACTIVE_CONFIG = _resolve_active_config()
_ACTIVE_PROFILE = ScenarioProfile(_ACTIVE_CONFIG)


class KubePredictUser(HttpUser):
    """Simulates a client sending bounded synthetic compute work to the KubePredict API."""

    host = _ACTIVE_CONFIG.host
    wait_time = between(0.05, 0.2)

    def on_start(self) -> None:
        """Perform a single startup health probe once per user."""
        try:
            with self.client.get(
                "/healthz",
                timeout=5.0,
                name="[startup] /healthz",
                catch_response=True,
            ) as resp:
                if resp.status_code != 200:
                    resp.failure(f"Startup health check failed with status code {resp.status_code}")
        except Exception:  # noqa: BLE001, S110
            # Do not crash the entire runner if one worker startup check encounters a transient network glitch
            pass

    @task
    def execute_work(self) -> None:
        """Issue a POST /work request with configured computational parameters."""
        request_id = str(uuid.uuid4())
        payload = {
            "cpu_iterations": _ACTIVE_CONFIG.cpu_iterations,
            "delay_ms": _ACTIVE_CONFIG.delay_ms,
            "payload_size": _ACTIVE_CONFIG.payload_size,
        }
        headers = {
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
        }

        try:
            with self.client.post(
                _ACTIVE_CONFIG.endpoint,
                json=payload,
                headers=headers,
                timeout=_ACTIVE_CONFIG.request_timeout_seconds,
                catch_response=True,
                name="POST /work",
            ) as resp:
                if resp.status_code == 200:
                    resp.success()
                elif resp.status_code == 422:
                    resp.failure(f"Validation failure (HTTP 422): {resp.text}")
                else:
                    resp.failure(f"Unexpected status code: {resp.status_code}")
        except StopUser:
            raise
        except Exception:  # noqa: BLE001, S110
            # Handled by Locust's catch_response / failure recording
            pass


class ScenarioLoadShape(DeterministicLoadShape):
    """Dynamic LoadTestShape reading the active scenario profile."""

    profile = _ACTIVE_PROFILE
