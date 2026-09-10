import hashlib
import platform
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import locust
from pydantic import BaseModel, ConfigDict, Field

from load_testing.config import ScenarioConfig

RunStatus = Literal["started", "completed", "failed", "interrupted"]


def compute_config_hash(content: str | bytes) -> str:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def get_git_info() -> tuple[str | None, bool | None]:
    """Retrieve Git commit hash and dirty status safely without throwing."""
    try:
        commit_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if commit_res.returncode != 0:
            return (None, None)
        commit = commit_res.stdout.strip()
        if not commit:
            return (None, None)

        status_res = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        dirty = bool(status_res.stdout.strip()) if status_res.returncode == 0 else None
        return (commit, dirty)
    except Exception:  # noqa: BLE001
        return (None, None)


def sanitize_run_id(raw_id: str) -> str:
    """Ensure run_id is filesystem-safe."""
    sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "_", raw_id)
    return sanitized.strip("_") or "run_default"


class RunMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    scenario_name: str
    started_at: datetime
    completed_at: datetime | None = None
    status: RunStatus = "started"
    host: str
    endpoint: str
    random_seed: int
    configuration_file: str
    configuration_hash: str
    total_duration_seconds: int
    stages: list[dict[str, Any]]
    cpu_iterations: int
    delay_ms: int
    payload_size: int
    requested_max_users: int
    spawn_rates: list[float]
    git_commit: str | None = None
    git_dirty: bool | None = None
    application_image: str | None = None
    cluster_name: str | None = None
    controller_mode: str | None = None
    locust_version: str
    python_version: str
    output_paths: dict[str, str] = Field(default_factory=dict)
    actual_requests: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None

    @classmethod
    def create_initial(
        cls,
        run_id: str,
        config: ScenarioConfig,
        config_path_str: str,
        config_content: str,
        application_image: str | None = None,
        cluster_name: str | None = None,
        controller_mode: str | None = None,
        notes: str | None = None,
    ) -> "RunMetadata":
        git_commit, git_dirty = get_git_info()
        now = datetime.now(UTC)
        clean_run_id = sanitize_run_id(run_id)

        max_users = max((s.target_users for s in config.stages), default=0)
        spawn_rates = [s.spawn_rate_per_second for s in config.stages]
        stages_data = [s.model_dump() for s in config.stages]

        return cls(
            run_id=clean_run_id,
            scenario_name=config.scenario_name,
            started_at=now,
            status="started",
            host=config.host,
            endpoint=config.endpoint,
            random_seed=config.random_seed,
            configuration_file=config_path_str,
            configuration_hash=compute_config_hash(config_content),
            total_duration_seconds=config.total_duration_seconds,
            stages=stages_data,
            cpu_iterations=config.cpu_iterations,
            delay_ms=config.delay_ms,
            payload_size=config.payload_size,
            requested_max_users=max_users,
            spawn_rates=spawn_rates,
            git_commit=git_commit,
            git_dirty=git_dirty,
            application_image=application_image,
            cluster_name=cluster_name,
            controller_mode=controller_mode,
            locust_version=locust.__version__,
            python_version=platform.python_version(),
            notes=notes,
        )

    def finalize(
        self,
        status: RunStatus,
        output_paths: dict[str, str] | None = None,
        actual_requests: dict[str, Any] | None = None,
        notes: str | None = None,
    ) -> None:
        self.completed_at = datetime.now(UTC)
        self.status = status
        if output_paths:
            self.output_paths.update(output_paths)
        if actual_requests:
            self.actual_requests.update(actual_requests)
        if notes:
            self.notes = f"{self.notes}\n{notes}" if self.notes else notes

    def save(self, file_path: Path) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(self.model_dump_json(indent=2))
