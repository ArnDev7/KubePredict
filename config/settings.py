from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.enums import ControllerMode


class Settings(BaseSettings):
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"
    max_cpu_iterations: int = Field(default=500000, gt=0, le=5000000)
    max_delay_ms: int = Field(default=2000, ge=0, le=10000)

    target_namespace: str = Field(default="apa-system", min_length=1)
    target_deployment: str = Field(default="workload-api", min_length=1)

    controller_mode: ControllerMode = ControllerMode.OBSERVE
    enable_live_scaling: bool = False
    minimum_replicas: int = 1
    maximum_replicas: int = 10
    maximum_scale_up_step: int = Field(default=3, gt=0)
    maximum_scale_down_step: int = Field(default=1, gt=0)
    cooldown_seconds: int = Field(default=60, ge=0)

    prometheus_url: str = "http://prometheus-operated.monitoring.svc:9090"
    telemetry_stale_after_seconds: int = Field(default=30, gt=0)

    slo_latency_ms: float = Field(default=500.0, gt=0.0)
    random_seed: int = 42

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        if self.minimum_replicas > self.maximum_replicas:
            raise ValueError("minimum_replicas must be <= maximum_replicas")
        if self.enable_live_scaling and self.controller_mode != ControllerMode.LIVE:
            raise ValueError("enable_live_scaling=true is invalid unless controller_mode=live")
        return self


def get_settings() -> Settings:
    return Settings()
