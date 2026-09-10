from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).parent.parent.parent / "kubernetes" / "base"


def _load_yaml(filename: str) -> dict[str, Any]:
    file_path = BASE_DIR / filename
    assert file_path.exists(), f"File {filename} does not exist in {BASE_DIR}"
    with open(file_path, "r", encoding="utf-8") as f:
        content: dict[str, Any] = yaml.safe_load(f)
        return content


def test_manifest_files_exist() -> None:
    expected_files = [
        "namespace.yaml",
        "configmap.yaml",
        "deployment.yaml",
        "service.yaml",
        "kustomization.yaml",
    ]
    for filename in expected_files:
        assert (BASE_DIR / filename).is_file(), f"Expected file {filename} missing"


def test_namespace_manifest() -> None:
    ns = _load_yaml("namespace.yaml")
    assert ns.get("apiVersion") == "v1"
    assert ns.get("kind") == "Namespace"
    assert ns["metadata"]["name"] == "kubepredict-system"
    assert ns["metadata"]["labels"]["app.kubernetes.io/name"] == "kubepredict"
    assert ns["metadata"]["labels"]["app.kubernetes.io/component"] == "workload-api"


def test_configmap_manifest() -> None:
    cm = _load_yaml("configmap.yaml")
    assert cm.get("apiVersion") == "v1"
    assert cm.get("kind") == "ConfigMap"
    assert cm["metadata"]["name"] == "kubepredict-workload-api-config"
    assert cm["metadata"]["namespace"] == "kubepredict-system"

    data = cm["data"]
    assert data["APP_HOST"] == "0.0.0.0"
    assert data["APP_PORT"] == "8000"
    assert data["LOG_LEVEL"] == "INFO"
    assert data["CONTROLLER_MODE"] == "observe"
    assert data["ENABLE_LIVE_SCALING"] == "false"
    assert data["TARGET_NAMESPACE"] == "kubepredict-system"
    assert data["TARGET_DEPLOYMENT"] == "kubepredict-workload-api"
    assert data["MINIMUM_REPLICAS"] == "1"
    assert data["MAXIMUM_REPLICAS"] == "10"
    assert data["MAXIMUM_SCALE_UP_STEP"] == "3"
    assert data["MAXIMUM_SCALE_DOWN_STEP"] == "1"
    assert data["COOLDOWN_SECONDS"] == "60"
    assert data["TELEMETRY_STALE_AFTER_SECONDS"] == "30"
    assert data["SLO_LATENCY_MS"] == "500.0"
    assert data["RANDOM_SEED"] == "42"


def test_deployment_manifest() -> None:
    dep = _load_yaml("deployment.yaml")
    assert dep.get("apiVersion") == "apps/v1"
    assert dep.get("kind") == "Deployment"
    assert dep["metadata"]["name"] == "kubepredict-workload-api"
    assert dep["metadata"]["namespace"] == "kubepredict-system"

    spec = dep["spec"]
    assert spec["replicas"] == 2
    assert spec["revisionHistoryLimit"] == 3
    assert spec["progressDeadlineSeconds"] == 120
    assert spec["minReadySeconds"] == 3

    # Strategy
    strategy = spec["strategy"]
    assert strategy["type"] == "RollingUpdate"
    assert strategy["rollingUpdate"]["maxUnavailable"] == 0
    assert strategy["rollingUpdate"]["maxSurge"] == 1

    # Selector
    selector = spec["selector"]["matchLabels"]
    assert selector == {
        "app.kubernetes.io/name": "kubepredict",
        "app.kubernetes.io/component": "workload-api",
    }

    # Template
    template = spec["template"]
    pod_labels = template["metadata"]["labels"]
    assert pod_labels["app.kubernetes.io/name"] == "kubepredict"
    assert pod_labels["app.kubernetes.io/component"] == "workload-api"

    pod_spec = template["spec"]
    assert pod_spec.get("automountServiceAccountToken") is False
    assert pod_spec.get("terminationGracePeriodSeconds") == 15

    # Security Context (Pod)
    pod_sec = pod_spec["securityContext"]
    assert pod_sec.get("runAsNonRoot") is True
    assert pod_sec.get("runAsUser") == 1000
    assert pod_sec.get("runAsGroup") == 1000
    assert pod_sec.get("seccompProfile", {}).get("type") == "RuntimeDefault"

    # Container
    containers = pod_spec["containers"]
    assert len(containers) == 1
    c = containers[0]
    assert c["name"] == "workload-api"
    assert c["image"] == "kubepredict-workload-api:0.1.0"
    assert "latest" not in c["image"]
    assert c["imagePullPolicy"] == "IfNotPresent"

    # Ports
    ports = c["ports"]
    assert len(ports) == 1
    assert ports[0]["containerPort"] == 8000
    assert ports[0]["name"] == "http"
    assert ports[0]["protocol"] == "TCP"

    # Resources
    resources = c["resources"]
    assert resources["requests"]["cpu"] == "100m"
    assert resources["requests"]["memory"] == "128Mi"
    assert resources["limits"]["cpu"] == "500m"
    assert resources["limits"]["memory"] == "256Mi"

    # Probes
    startup_probe = c["startupProbe"]
    assert startup_probe["httpGet"]["path"] == "/healthz"
    assert startup_probe["httpGet"]["port"] == "http"

    liveness_probe = c["livenessProbe"]
    assert liveness_probe["httpGet"]["path"] == "/healthz"
    assert liveness_probe["httpGet"]["port"] == "http"

    readiness_probe = c["readinessProbe"]
    assert readiness_probe["httpGet"]["path"] == "/readyz"
    assert readiness_probe["httpGet"]["port"] == "http"

    # Container Security Context
    c_sec = c["securityContext"]
    assert c_sec.get("allowPrivilegeEscalation") is False
    assert c_sec.get("readOnlyRootFilesystem") is True
    assert c_sec.get("runAsNonRoot") is True
    assert c_sec.get("capabilities", {}).get("drop") == ["ALL"]
    assert c_sec.get("privileged") is not True

    # Volumes & Mounts
    volumes = pod_spec.get("volumes", [])
    assert any(v["name"] == "tmp" and "emptyDir" in v for v in volumes)
    assert not any("hostPath" in v for v in volumes)

    volume_mounts = c.get("volumeMounts", [])
    assert any(vm["name"] == "tmp" and vm["mountPath"] == "/tmp" for vm in volume_mounts)

    # Absence of unsafe pod features
    assert pod_spec.get("hostNetwork") is not True
    assert pod_spec.get("hostPID") is not True
    assert pod_spec.get("hostIPC") is not True


def test_service_manifest() -> None:
    svc = _load_yaml("service.yaml")
    assert svc.get("apiVersion") == "v1"
    assert svc.get("kind") == "Service"
    assert svc["metadata"]["name"] == "kubepredict-workload-api"
    assert svc["metadata"]["namespace"] == "kubepredict-system"

    spec = svc["spec"]
    assert spec.get("type") == "ClusterIP"
    assert spec["selector"] == {
        "app.kubernetes.io/name": "kubepredict",
        "app.kubernetes.io/component": "workload-api",
    }

    ports = spec["ports"]
    assert len(ports) == 1
    assert ports[0]["port"] == 8000
    assert ports[0]["targetPort"] == "http"
    assert ports[0]["protocol"] == "TCP"
    assert ports[0]["name"] == "http"


def test_kustomization_manifest() -> None:
    kust = _load_yaml("kustomization.yaml")
    assert kust.get("apiVersion") in [
        "kustomize.config.k8s.io/v1beta1",
        "kustomize.config.k8s.io/v1alpha1",
    ]
    assert kust.get("kind") == "Kustomization"
    resources = kust.get("resources", [])
    assert resources == [
        "namespace.yaml",
        "configmap.yaml",
        "deployment.yaml",
        "service.yaml",
    ]


def test_no_forbidden_manifests() -> None:
    forbidden_stems = [
        "hpa",
        "secret",
        "rbac",
        "servicemonitor",
        "prometheusrule",
        "ingress",
    ]
    for p in BASE_DIR.glob("*.yaml"):
        stem = p.stem.lower()
        for forbidden in forbidden_stems:
            assert forbidden not in stem, f"Forbidden manifest {p.name} found in base directory"
