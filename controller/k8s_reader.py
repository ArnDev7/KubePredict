"""Read-only Kubernetes deployment status reader for KubePredict controller (Phase 9B).

Strictly read-only: executes fixed kubectl get deployment commands via subprocess
with shell=False, bounded timeouts, and strict schema validation.
Contains zero Kubernetes API write calls, zero client library imports, and zero mutations.
"""

import json
import logging
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT: str = "kind-kubepredict-dev"
DEFAULT_NAMESPACE: str = "kubepredict-system"
DEFAULT_DEPLOYMENT: str = "kubepredict-workload-api"
READ_TIMEOUT_SECONDS: float = 10.0


@dataclass(frozen=True)
class DeploymentReplicaStatus:
    """Read-only snapshot of deployment replica status."""

    is_valid: bool
    desired_replicas: int | None
    available_replicas: int | None
    ready_replicas: int | None
    error_message: str | None = None


class ReadOnlyDeploymentReader:
    """Strictly read-only reader for Kubernetes Deployment replica counts."""

    def __init__(
        self,
        context: str = DEFAULT_CONTEXT,
        namespace: str = DEFAULT_NAMESPACE,
        deployment_name: str = DEFAULT_DEPLOYMENT,
        timeout_seconds: float = READ_TIMEOUT_SECONDS,
    ) -> None:
        self.context = context
        self.namespace = namespace
        self.deployment_name = deployment_name
        self.timeout_seconds = timeout_seconds

    def get_replica_status(self) -> DeploymentReplicaStatus:
        """Fetch current deployment replicas via strictly read-only kubectl get.

        Returns
        -------
        DeploymentReplicaStatus
            Validated replica counts or explicit error details.
        """
        cmd = [
            "kubectl",
            "get",
            "deployment",
            self.deployment_name,
            "--context",
            self.context,
            "--namespace",
            self.namespace,
            "-o",
            "json",
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
            msg = f"kubectl get deployment timed out after {self.timeout_seconds}s"
            logger.warning(msg)
            return DeploymentReplicaStatus(
                is_valid=False,
                desired_replicas=None,
                available_replicas=None,
                ready_replicas=None,
                error_message=msg,
            )
        except FileNotFoundError:
            msg = "kubectl executable not found in PATH"
            logger.error(msg)
            return DeploymentReplicaStatus(
                is_valid=False,
                desired_replicas=None,
                available_replicas=None,
                ready_replicas=None,
                error_message=msg,
            )
        except Exception as e:  # noqa: BLE001
            msg = f"Unexpected error executing kubectl: {e}"
            logger.error(msg)
            return DeploymentReplicaStatus(
                is_valid=False,
                desired_replicas=None,
                available_replicas=None,
                ready_replicas=None,
                error_message=msg,
            )

        if res.returncode != 0:
            err = res.stderr.strip() or f"exit code {res.returncode}"
            msg = f"kubectl get deployment failed: {err}"
            logger.warning(msg)
            return DeploymentReplicaStatus(
                is_valid=False,
                desired_replicas=None,
                available_replicas=None,
                ready_replicas=None,
                error_message=msg,
            )

        try:
            payload = json.loads(res.stdout)
        except json.JSONDecodeError as e:
            msg = f"Failed to parse kubectl JSON output: {e}"
            logger.warning(msg)
            return DeploymentReplicaStatus(
                is_valid=False,
                desired_replicas=None,
                available_replicas=None,
                ready_replicas=None,
                error_message=msg,
            )

        # Validate structure and extract replica counts
        spec = payload.get("spec", {})
        status = payload.get("status", {})

        desired = spec.get("replicas")
        available = status.get("availableReplicas")
        ready = status.get("readyReplicas", 0)

        if desired is None or not isinstance(desired, int) or desired < 0:
            msg = f"Invalid or missing spec.replicas: {desired}"
            logger.warning(msg)
            return DeploymentReplicaStatus(
                is_valid=False,
                desired_replicas=None,
                available_replicas=None,
                ready_replicas=None,
                error_message=msg,
            )

        # In Kubernetes, availableReplicas is absent from status when 0 pods are available
        if available is None:
            available = 0
        elif not isinstance(available, int) or available < 0:
            msg = f"Invalid status.availableReplicas: {available}"
            logger.warning(msg)
            return DeploymentReplicaStatus(
                is_valid=False,
                desired_replicas=desired,
                available_replicas=None,
                ready_replicas=None,
                error_message=msg,
            )

        if not isinstance(ready, int) or ready < 0:
            ready = 0

        return DeploymentReplicaStatus(
            is_valid=True,
            desired_replicas=desired,
            available_replicas=available,
            ready_replicas=ready,
            error_message=None,
        )
