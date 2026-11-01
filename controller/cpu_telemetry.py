"""Read-only CPU telemetry reader for KubePredict controller (Phase 10B).

Collects CPU utilization across workload pods via read-only kubectl top pod
without Kubernetes client library imports, mutation verbs, or shell=True.
"""

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT: str = "kind-kubepredict-dev"
DEFAULT_NAMESPACE: str = "kubepredict-system"
DEFAULT_SELECTOR: str = "app.kubernetes.io/name=kubepredict"
WORKLOAD_POD_CPU_REQUEST_CORES: float = 0.100  # 100m CPU request from deployment spec
READ_TIMEOUT_SECONDS: float = 8.0
MAX_FRESHNESS_AGE_SECONDS: float = 60.0


def parse_cpu_quantity(quantity_str: str) -> float:
    """Parse a Kubernetes CPU quantity string into decimal CPU cores.

    Examples:
    - '2m' -> 0.002 cores
    - '500u' -> 0.0005 cores
    - '2000000n' -> 0.002 cores
    - '1' -> 1.0 cores
    """
    s = quantity_str.strip()
    if not s:
        raise ValueError("Empty CPU quantity string")

    if s.endswith("n"):
        return float(s[:-1]) / 1e9
    if s.endswith("u"):
        return float(s[:-1]) / 1e6
    if s.endswith("m"):
        return float(s[:-1]) / 1e3
    return float(s)


@dataclass(frozen=True)
class CpuTelemetryResult:
    """Snapshot of aggregated read-only CPU telemetry."""

    timestamp: datetime
    is_available: bool
    is_fresh: bool
    pod_count: int
    total_cpu_cores: float | None = None
    requested_cpu_cores: float | None = None
    cpu_utilization_ratio: float | None = None
    pod_metrics: dict[str, float] = field(default_factory=dict)
    error_message: str | None = None
    warnings: list[str] = field(default_factory=list)


class CpuTelemetryProvider(Protocol):
    """Protocol for read-only CPU telemetry providers enabling test dependency injection."""

    def get_cpu_telemetry(self) -> CpuTelemetryResult:
        """Fetch current CPU telemetry."""
        ...


class ReadOnlyCpuTelemetryReader:
    """Strictly read-only reader for workload pod CPU utilization."""

    def __init__(
        self,
        context: str = DEFAULT_CONTEXT,
        namespace: str = DEFAULT_NAMESPACE,
        selector: str = DEFAULT_SELECTOR,
        cpu_request_cores: float = WORKLOAD_POD_CPU_REQUEST_CORES,
        timeout_seconds: float = READ_TIMEOUT_SECONDS,
        max_age_seconds: float = MAX_FRESHNESS_AGE_SECONDS,
    ) -> None:
        self.context = context
        self.namespace = namespace
        self.selector = selector
        self.cpu_request_cores = cpu_request_cores
        self.timeout_seconds = timeout_seconds
        self.max_age_seconds = max_age_seconds

    def get_cpu_telemetry(self) -> CpuTelemetryResult:
        """Fetch pod CPU metrics via strictly read-only kubectl top pod.

        Returns
        -------
        CpuTelemetryResult
            Structured CPU telemetry snapshot.
        """
        now = datetime.now(UTC)

        cmd = [
            "kubectl",
            "top",
            "pod",
            "-l",
            self.selector,
            "--context",
            self.context,
            "--namespace",
            self.namespace,
            "--no-headers",
        ]

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                shell=False,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            msg = f"kubectl top pod timed out after {self.timeout_seconds}s"
            logger.warning(msg)
            return CpuTelemetryResult(
                timestamp=now,
                is_available=False,
                is_fresh=False,
                pod_count=0,
                error_message=msg,
            )
        except FileNotFoundError:
            msg = "kubectl executable not found in PATH"
            logger.error(msg)
            return CpuTelemetryResult(
                timestamp=now,
                is_available=False,
                is_fresh=False,
                pod_count=0,
                error_message=msg,
            )
        except Exception as e:  # noqa: BLE001
            msg = f"Unexpected error reading CPU telemetry: {e}"
            logger.error(msg)
            return CpuTelemetryResult(
                timestamp=now,
                is_available=False,
                is_fresh=False,
                pod_count=0,
                error_message=msg,
            )

        if res.returncode != 0:
            err = res.stderr.strip() or f"exit code {res.returncode}"
            msg = f"kubectl top pod failed: {err}"
            logger.warning(msg)
            return CpuTelemetryResult(
                timestamp=now,
                is_available=False,
                is_fresh=False,
                pod_count=0,
                error_message=msg,
            )

        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        if not lines:
            msg = "No workload pods found in kubectl top pod output"
            logger.warning(msg)
            return CpuTelemetryResult(
                timestamp=now,
                is_available=False,
                is_fresh=False,
                pod_count=0,
                error_message=msg,
            )

        pod_metrics: dict[str, float] = {}
        total_cores = 0.0

        for line in lines:
            parts = line.split()
            if len(parts) < 2:
                continue
            pod_name = parts[0]
            raw_cpu = parts[1]
            try:
                cores = parse_cpu_quantity(raw_cpu)
            except Exception as e:  # noqa: BLE001
                msg = f"Failed to parse CPU quantity '{raw_cpu}' for pod {pod_name}: {e}"
                logger.warning(msg)
                return CpuTelemetryResult(
                    timestamp=now,
                    is_available=False,
                    is_fresh=False,
                    pod_count=len(lines),
                    error_message=msg,
                )

            pod_metrics[pod_name] = cores
            total_cores += cores

        pod_count = len(pod_metrics)
        if pod_count == 0:
            return CpuTelemetryResult(
                timestamp=now,
                is_available=False,
                is_fresh=False,
                pod_count=0,
                error_message="Parsed 0 valid pod metrics",
            )

        requested_cores = pod_count * self.cpu_request_cores
        if requested_cores <= 0.0:
            return CpuTelemetryResult(
                timestamp=now,
                is_available=False,
                is_fresh=False,
                pod_count=pod_count,
                error_message="Requested CPU cores must be > 0",
            )

        ratio = total_cores / requested_cores

        return CpuTelemetryResult(
            timestamp=now,
            is_available=True,
            is_fresh=True,
            pod_count=pod_count,
            total_cpu_cores=total_cores,
            requested_cpu_cores=requested_cores,
            cpu_utilization_ratio=ratio,
            pod_metrics=pod_metrics,
            error_message=None,
        )
