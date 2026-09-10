"""Read-only Kubernetes observation collector for workload status and resource telemetry."""

import json
import logging
import re
import subprocess
from datetime import UTC, datetime

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class PodObservation(BaseModel):
    """Observation of an individual workload pod."""

    pod_name: str
    phase: str
    ready: bool
    restart_count: int = Field(ge=0)
    cpu_millicores: float | None = None
    memory_bytes: int | None = None


class KubernetesSnapshot(BaseModel):
    """Point-in-time snapshot of the Kubernetes workload deployment and pods."""

    timestamp: datetime
    deployment_name: str
    namespace: str
    desired_replicas: int = Field(ge=0)
    available_replicas: int = Field(ge=0)
    ready_replicas: int = Field(ge=0)
    pods: list[PodObservation] = Field(default_factory=list)


def parse_cpu_to_millicores(raw: str) -> float | None:
    """Parse raw CPU string from kubectl top (e.g. '5m', '100m', '1') into millicores."""
    if not raw:
        return None
    raw = raw.strip()
    if raw.endswith("m"):
        try:
            return float(raw[:-1])
        except ValueError:
            return None
    elif raw.endswith("u"):  # microcores
        try:
            return float(raw[:-1]) / 1000.0
        except ValueError:
            return None
    elif raw.endswith("n"):  # nanocores
        try:
            return float(raw[:-1]) / 1000000.0
        except ValueError:
            return None
    else:
        try:
            return float(raw) * 1000.0
        except ValueError:
            return None


def parse_memory_to_bytes(raw: str) -> int | None:
    """Parse raw memory string from kubectl top (e.g. '55Mi', '100Ki', '1Gi') into bytes."""
    if not raw:
        return None
    raw = raw.strip()
    units = {
        "ki": 1024,
        "mi": 1024 * 1024,
        "gi": 1024 * 1024 * 1024,
        "k": 1000,
        "m": 1000 * 1000,
        "g": 1000 * 1000 * 1000,
    }
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?$", raw)
    if not match:
        return None
    num_str, unit_str = match.groups()
    num = float(num_str)
    if not unit_str:
        return int(num)
    mult = units.get(unit_str.lower(), 1)
    return int(num * mult)


class KubernetesCollector:
    """Collects read-only deployment and pod metrics using subprocess argument lists."""

    def __init__(
        self,
        context: str = "kind-kubepredict-dev",
        namespace: str = "kubepredict-system",
        deployment_name: str = "kubepredict-workload-api",
    ) -> None:
        self.context = context
        self.namespace = namespace
        self.deployment_name = deployment_name

    def collect_snapshot(self) -> KubernetesSnapshot:
        """Capture a complete snapshot of the deployment status and pod metrics."""
        now = datetime.now(UTC)
        dep_data = self._get_deployment_json()
        pods_data = self._get_pods_json()
        top_metrics = self._get_top_pods_metrics()

        spec = dep_data.get("spec", {})
        status = dep_data.get("status", {})

        desired = int(spec.get("replicas", 0))
        available = int(status.get("availableReplicas", 0))
        ready = int(status.get("readyReplicas", 0))

        pod_observations = []
        for pod in pods_data.get("items", []):
            p_metadata = pod.get("metadata", {})
            p_status = pod.get("status", {})
            p_name = p_metadata.get("name", "unknown")
            phase = p_status.get("phase", "Unknown")

            # Check readiness from containerStatuses
            container_statuses = p_status.get("containerStatuses", [])
            is_ready = any(cs.get("ready", False) for cs in container_statuses)
            restart_count = sum(cs.get("restartCount", 0) for cs in container_statuses)

            cpu_val, mem_val = top_metrics.get(p_name, (None, None))

            pod_observations.append(
                PodObservation(
                    pod_name=p_name,
                    phase=phase,
                    ready=is_ready,
                    restart_count=restart_count,
                    cpu_millicores=cpu_val,
                    memory_bytes=mem_val,
                )
            )

        return KubernetesSnapshot(
            timestamp=now,
            deployment_name=self.deployment_name,
            namespace=self.namespace,
            desired_replicas=desired,
            available_replicas=available,
            ready_replicas=ready,
            pods=pod_observations,
        )

    def _get_deployment_json(self) -> dict:
        cmd = [
            "kubectl",
            "get",
            "deployment",
            self.deployment_name,
            "-n",
            self.namespace,
            "--context",
            self.context,
            "-o",
            "json",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(res.stdout)

    def _get_pods_json(self) -> dict:
        cmd = [
            "kubectl",
            "get",
            "pods",
            "-n",
            self.namespace,
            "--context",
            self.context,
            "-l",
            "app.kubernetes.io/component=workload-api",
            "-o",
            "json",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(res.stdout)

    def _get_top_pods_metrics(self) -> dict[str, tuple[float | None, int | None]]:
        cmd = [
            "kubectl",
            "top",
            "pods",
            "-n",
            self.namespace,
            "--context",
            self.context,
            "--no-headers",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                logger.warning(f"kubectl top failed with code {res.returncode}: {res.stderr}")
                return {}

            results = {}
            for line in res.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 3:
                    p_name, raw_cpu, raw_mem = parts[0], parts[1], parts[2]
                    cpu_m = parse_cpu_to_millicores(raw_cpu)
                    mem_b = parse_memory_to_bytes(raw_mem)
                    results[p_name] = (cpu_m, mem_b)
            return results
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            logger.warning(f"Failed to fetch kubectl top metrics: {exc}")
            return {}
