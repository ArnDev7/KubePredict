"""Unit tests for Kubernetes observation collector and string parsers."""

from datetime import UTC

from experiments.telemetry.collector import (
    KubernetesCollector,
    parse_cpu_to_millicores,
    parse_memory_to_bytes,
)


def test_parse_cpu_to_millicores():
    assert parse_cpu_to_millicores("3m") == 3.0
    assert parse_cpu_to_millicores("100m") == 100.0
    assert parse_cpu_to_millicores("1") == 1000.0
    assert parse_cpu_to_millicores("0.5") == 500.0
    assert parse_cpu_to_millicores("500u") == 0.5
    assert parse_cpu_to_millicores("") is None
    assert parse_cpu_to_millicores("invalid") is None


def test_parse_memory_to_bytes():
    assert parse_memory_to_bytes("55Mi") == 55 * 1024 * 1024
    assert parse_memory_to_bytes("100Ki") == 100 * 1024
    assert parse_memory_to_bytes("1Gi") == 1024 * 1024 * 1024
    assert parse_memory_to_bytes("1000") == 1000
    assert parse_memory_to_bytes("") is None
    assert parse_memory_to_bytes("invalid") is None


def test_collect_snapshot_mocked(monkeypatch):
    collector = KubernetesCollector(
        context="test-ctx",
        namespace="test-ns",
        deployment_name="test-deploy",
    )

    mock_dep = {
        "spec": {"replicas": 2},
        "status": {"availableReplicas": 2, "readyReplicas": 2},
    }

    mock_pods = {
        "items": [
            {
                "metadata": {"name": "pod-1"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"ready": True, "restartCount": 0}],
                },
            },
            {
                "metadata": {"name": "pod-2"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"ready": True, "restartCount": 1}],
                },
            },
        ]
    }

    mock_top = {
        "pod-1": (12.5, 60 * 1024 * 1024),
        "pod-2": (8.0, 58 * 1024 * 1024),
    }

    monkeypatch.setattr(collector, "_get_deployment_json", lambda: mock_dep)
    monkeypatch.setattr(collector, "_get_pods_json", lambda: mock_pods)
    monkeypatch.setattr(collector, "_get_top_pods_metrics", lambda: mock_top)

    snapshot = collector.collect_snapshot()

    assert snapshot.deployment_name == "test-deploy"
    assert snapshot.desired_replicas == 2
    assert snapshot.available_replicas == 2
    assert snapshot.ready_replicas == 2
    assert snapshot.timestamp.tzinfo == UTC
    assert len(snapshot.pods) == 2

    p1 = snapshot.pods[0]
    assert p1.pod_name == "pod-1"
    assert p1.ready is True
    assert p1.restart_count == 0
    assert p1.cpu_millicores == 12.5
    assert p1.memory_bytes == 60 * 1024 * 1024

    p2 = snapshot.pods[1]
    assert p2.pod_name == "pod-2"
    assert p2.ready is True
    assert p2.restart_count == 1
