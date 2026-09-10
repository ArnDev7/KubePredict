import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from experiments.telemetry.collector import KubernetesSnapshot
from experiments.telemetry.orchestrator import ScenarioCaptureOrchestrator


def test_orchestrator_context_guard(tmp_path: Path, monkeypatch):
    scenario_file = Path("load_testing/scenarios/steady.yaml")
    orchestrator = ScenarioCaptureOrchestrator(
        scenario_path=scenario_file,
        raw_output_root=tmp_path,
        context="kind-kubepredict-dev",
    )

    # Mock kubectl config current-context to return a wrong context
    def mock_run(cmd, *args, **kwargs):
        if "current-context" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="minikube\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    with pytest.raises(RuntimeError, match="Context mismatch"):
        orchestrator.execute()


def test_orchestrator_hpa_guard(tmp_path: Path, monkeypatch):
    scenario_file = Path("load_testing/scenarios/steady.yaml")
    orchestrator = ScenarioCaptureOrchestrator(
        scenario_path=scenario_file,
        raw_output_root=tmp_path,
    )

    # Mock kubectl config to match, collector snapshot to 2, but HPA to return an active HPA
    def mock_run(cmd, *args, **kwargs):
        if "current-context" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="kind-kubepredict-dev\n", stderr="")
        if "get" in cmd and "hpa" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="kubepredict-workload-api   Deployment/kubepredict-workload-api",
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    mock_snap = KubernetesSnapshot(
        timestamp=datetime.now(UTC),
        deployment_name="kubepredict-workload-api",
        namespace="kubepredict-system",
        desired_replicas=2,
        available_replicas=2,
        ready_replicas=2,
    )
    monkeypatch.setattr(orchestrator.collector, "collect_snapshot", lambda: mock_snap)
    monkeypatch.setattr(subprocess, "run", mock_run)

    with pytest.raises(RuntimeError, match="Live HPA detected"):
        orchestrator.execute()


def test_orchestrator_fixed_replica_guard(tmp_path: Path, monkeypatch):
    scenario_file = Path("load_testing/scenarios/steady.yaml")
    orchestrator = ScenarioCaptureOrchestrator(
        scenario_path=scenario_file,
        raw_output_root=tmp_path,
    )

    def mock_run(cmd, *args, **kwargs):
        if "current-context" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="kind-kubepredict-dev\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    # Desired replicas = 4 (invariant violated)
    mock_snap = KubernetesSnapshot(
        timestamp=datetime.now(UTC),
        deployment_name="kubepredict-workload-api",
        namespace="kubepredict-system",
        desired_replicas=4,
        available_replicas=4,
        ready_replicas=4,
    )
    monkeypatch.setattr(orchestrator.collector, "collect_snapshot", lambda: mock_snap)
    monkeypatch.setattr(subprocess, "run", mock_run)

    with pytest.raises(RuntimeError, match="Fixed replica invariant violated"):
        orchestrator.execute()
