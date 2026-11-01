"""Unit tests for read-only Kubernetes deployment reader (Phase 9B)."""

import json
import subprocess
from unittest.mock import patch

from controller.k8s_reader import (
    DEFAULT_CONTEXT,
    DEFAULT_DEPLOYMENT,
    DEFAULT_NAMESPACE,
    ReadOnlyDeploymentReader,
)


def test_valid_deployment_json():
    reader = ReadOnlyDeploymentReader()
    fake_output = {
        "spec": {"replicas": 2},
        "status": {"availableReplicas": 2, "readyReplicas": 2},
    }

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(fake_output),
            stderr="",
        )
        status = reader.get_replica_status()

    assert status.is_valid is True
    assert status.desired_replicas == 2
    assert status.available_replicas == 2
    assert (
        status.readyReplicas == 2
        if hasattr(status, "readyReplicas")
        else status.ready_replicas == 2
    )
    assert status.error_message is None

    # Check subprocess args
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd == [
        "kubectl",
        "get",
        "deployment",
        DEFAULT_DEPLOYMENT,
        "--context",
        DEFAULT_CONTEXT,
        "--namespace",
        DEFAULT_NAMESPACE,
        "-o",
        "json",
    ]
    assert mock_run.call_args[1]["shell"] is False


def test_absent_available_replicas_defaults_to_zero():
    reader = ReadOnlyDeploymentReader()
    fake_output = {
        "spec": {"replicas": 3},
        "status": {},  # In K8s, 0 available pods means key is absent
    }

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(fake_output),
            stderr="",
        )
        status = reader.get_replica_status()

    assert status.is_valid is True
    assert status.desired_replicas == 3
    assert status.available_replicas == 0


def test_missing_desired_replicas():
    reader = ReadOnlyDeploymentReader()
    fake_output = {
        "spec": {},
        "status": {"availableReplicas": 2},
    }

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(fake_output),
            stderr="",
        )
        status = reader.get_replica_status()

    assert status.is_valid is False
    assert "Invalid or missing spec.replicas" in (status.error_message or "")


def test_invalid_json():
    reader = ReadOnlyDeploymentReader()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="not-json-content",
            stderr="",
        )
        status = reader.get_replica_status()

    assert status.is_valid is False
    assert "Failed to parse kubectl JSON" in (status.error_message or "")


def test_subprocess_timeout():
    reader = ReadOnlyDeploymentReader(timeout_seconds=5.0)

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="kubectl", timeout=5.0)):
        status = reader.get_replica_status()

    assert status.is_valid is False
    assert "timed out" in (status.error_message or "")


def test_subprocess_nonzero_exit():
    reader = ReadOnlyDeploymentReader()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="Error from server (NotFound): deployments.apps 'kubepredict-workload-api' not found",
        )
        status = reader.get_replica_status()

    assert status.is_valid is False
    assert "kubectl get deployment failed" in (status.error_message or "")


def test_kubectl_not_found():
    reader = ReadOnlyDeploymentReader()

    with patch("subprocess.run", side_effect=FileNotFoundError("kubectl not found")):
        status = reader.get_replica_status()

    assert status.is_valid is False
    assert "executable not found" in (status.error_message or "")
