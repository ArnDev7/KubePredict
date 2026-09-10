import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator


def _validate_not_boolean(field_name: str, value: Any) -> None:
    if isinstance(value, bool):
        raise ValueError(f"Field '{field_name}' must be an integer, not a boolean.")


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


class StageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_seconds: int
    target_users: int
    spawn_rate_per_second: float

    @field_validator("duration_seconds", mode="before")
    @classmethod
    def validate_duration_type(cls, v: Any) -> Any:
        _validate_not_boolean("duration_seconds", v)
        return v

    @field_validator("target_users", mode="before")
    @classmethod
    def validate_users_type(cls, v: Any) -> Any:
        _validate_not_boolean("target_users", v)
        return v

    @field_validator("duration_seconds")
    @classmethod
    def validate_duration(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Stage duration_seconds must be positive (> 0).")
        return v

    @field_validator("target_users")
    @classmethod
    def validate_target_users(cls, v: int) -> int:
        if v < 0 or v > 50:
            raise ValueError("Stage target_users must be between 0 and 50.")
        return v

    @field_validator("spawn_rate_per_second")
    @classmethod
    def validate_spawn_rate(cls, v: float) -> float:
        if v <= 0 or v > 10.0:
            raise ValueError("Stage spawn_rate_per_second must be > 0 and <= 10.0.")
        return v


class ScenarioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_name: str
    description: str
    total_duration_seconds: int
    host: str = "http://127.0.0.1:8000"
    endpoint: str = "/work"
    random_seed: int = 42
    cpu_iterations: int = 100000
    delay_ms: int = 0
    payload_size: int = 0
    request_timeout_seconds: float = 10.0
    stages: list[StageConfig]

    @field_validator("total_duration_seconds", mode="before")
    @classmethod
    def validate_total_duration_type(cls, v: Any) -> Any:
        _validate_not_boolean("total_duration_seconds", v)
        return v

    @field_validator("random_seed", mode="before")
    @classmethod
    def validate_seed_type(cls, v: Any) -> Any:
        _validate_not_boolean("random_seed", v)
        return v

    @field_validator("cpu_iterations", mode="before")
    @classmethod
    def validate_cpu_type(cls, v: Any) -> Any:
        _validate_not_boolean("cpu_iterations", v)
        return v

    @field_validator("delay_ms", mode="before")
    @classmethod
    def validate_delay_type(cls, v: Any) -> Any:
        _validate_not_boolean("delay_ms", v)
        return v

    @field_validator("payload_size", mode="before")
    @classmethod
    def validate_payload_type(cls, v: Any) -> Any:
        _validate_not_boolean("payload_size", v)
        return v

    @field_validator("scenario_name")
    @classmethod
    def validate_scenario_name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("scenario_name must be non-empty.")
        if not re.match(r"^[a-zA-Z0-9_\-]+$", s):
            raise ValueError(
                "scenario_name must contain only alphanumeric characters, dashes, or underscores."
            )
        return s

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("description must be non-empty.")
        return s

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        if v != "/work":
            raise ValueError("endpoint must equal '/work' for experiment scenarios.")
        return v

    @field_validator("cpu_iterations")
    @classmethod
    def validate_cpu_iterations(cls, v: int) -> int:
        if v < 0 or v > 400000:
            raise ValueError("cpu_iterations must be between 0 and 400,000.")
        return v

    @field_validator("delay_ms")
    @classmethod
    def validate_delay_ms(cls, v: int) -> int:
        if v < 0 or v > 1000:
            raise ValueError("delay_ms must be between 0 and 1,000.")
        return v

    @field_validator("payload_size")
    @classmethod
    def validate_payload_size(cls, v: int) -> int:
        if v < 0 or v > 1000:
            raise ValueError("payload_size must be between 0 and 1,000.")
        return v

    @field_validator("request_timeout_seconds")
    @classmethod
    def validate_request_timeout(cls, v: float) -> float:
        if v <= 0 or v > 30.0:
            raise ValueError("request_timeout_seconds must be > 0 and <= 30.0.")
        return v

    @field_validator("stages")
    @classmethod
    def validate_stages_non_empty(cls, v: list[StageConfig]) -> list[StageConfig]:
        if not v:
            raise ValueError("stages list must not be empty.")
        return v

    @model_validator(mode="after")
    def validate_total_duration_and_stages(self) -> "ScenarioConfig":
        stage_sum = sum(s.duration_seconds for s in self.stages)
        if self.total_duration_seconds != stage_sum:
            raise ValueError(
                f"total_duration_seconds ({self.total_duration_seconds}) must equal sum of stage durations ({stage_sum})."
            )
        if self.total_duration_seconds <= 0 or self.total_duration_seconds > 900:
            raise ValueError(
                f"total_duration_seconds ({self.total_duration_seconds}) must be > 0 and <= 900."
            )
        return self

    def validate_host_safety(self, allow_non_local_host: bool = False) -> None:
        parsed = urlparse(self.host)
        if not parsed.scheme or parsed.scheme not in {"http", "https"}:
            raise ValueError(f"Invalid host URL scheme: '{parsed.scheme}'. Must be http or https.")

        hostname = (parsed.hostname or "").lower()
        if not hostname:
            raise ValueError("Host URL must contain a valid hostname.")

        # Reject wildcard hosts
        if hostname in {"0.0.0.0", "*", "::"}:
            raise ValueError(f"Wildcard host '{hostname}' is strictly prohibited.")

        if not allow_non_local_host and hostname not in LOCAL_HOSTS:
            raise ValueError(
                f"External host '{self.host}' rejected by default. "
                "Specify --allow-non-local-host if targeting an explicit development host."
            )


def load_scenario_config(
    path_or_dict: Path | str | dict[str, Any],
    allow_non_local_host: bool = False,
    host_override: str | None = None,
) -> ScenarioConfig:
    """Load and validate a ScenarioConfig from YAML file path or dict."""
    if isinstance(path_or_dict, (Path, str)):
        path = Path(path_or_dict)
        if not path.is_file():
            raise FileNotFoundError(f"Scenario configuration file not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if not isinstance(data, dict):
                raise ValueError(f"Scenario configuration in {path} must be a YAML mapping.")
    else:
        data = dict(path_or_dict)

    if host_override is not None:
        data["host"] = host_override

    config = ScenarioConfig.model_validate(data)
    config.validate_host_safety(allow_non_local_host=allow_non_local_host)
    return config
