from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
METRICS_SERVER_DIR = REPO_ROOT / "kubernetes" / "metrics-server"
SCRIPTS_DIR = REPO_ROOT / "scripts" / "kubernetes"


def _load_all_yaml(filename: str) -> list[dict[str, Any]]:
    file_path = METRICS_SERVER_DIR / filename
    assert file_path.exists(), f"File {filename} does not exist in {METRICS_SERVER_DIR}"
    with open(file_path, "r", encoding="utf-8") as f:
        docs = list(yaml.safe_load_all(f))
        return [d for d in docs if d is not None]


def test_metrics_server_directory_structure() -> None:
    assert METRICS_SERVER_DIR.is_dir(), "kubernetes/metrics-server directory must exist"
    expected_files = ["upstream.yaml", "patch-kind.yaml", "kustomization.yaml"]
    for fname in expected_files:
        assert (METRICS_SERVER_DIR / fname).is_file(), f"Expected file {fname} missing"


def test_upstream_manifest_parses() -> None:
    docs = _load_all_yaml("upstream.yaml")
    assert len(docs) > 0, "upstream.yaml must contain valid kubernetes documents"

    kinds = [doc.get("kind") for doc in docs]
    assert "Deployment" in kinds
    assert "APIService" in kinds
    assert "Service" in kinds
    assert "ServiceAccount" in kinds

    # Verify explicit versioned image
    deployment_doc = next(d for d in docs if d.get("kind") == "Deployment")
    container = deployment_doc["spec"]["template"]["spec"]["containers"][0]
    image = container["image"]
    assert "metrics-server" in image
    assert "latest" not in image
    assert ":" in image or "@sha256:" in image


def test_patch_kind_structure() -> None:
    docs = _load_all_yaml("patch-kind.yaml")
    assert len(docs) == 1, "patch-kind.yaml should contain exactly one document"
    patch = docs[0]
    assert patch.get("kind") == "Deployment"
    assert patch.get("metadata", {}).get("name") == "metrics-server"
    assert patch.get("metadata", {}).get("namespace") == "kube-system"

    args = patch["spec"]["template"]["spec"]["containers"][0]["args"]
    assert args.count("--kubelet-insecure-tls") == 1
    # Verify no dangerous flags are introduced
    assert not any("disable-authentication" in a.lower() for a in args)
    assert not any("disable-authorization" in a.lower() for a in args)


def test_kustomization_parses_and_has_no_forbidden_resources() -> None:
    with open(METRICS_SERVER_DIR / "kustomization.yaml", "r", encoding="utf-8") as f:
        kust = yaml.safe_load(f)

    assert kust.get("kind") == "Kustomization"
    assert kust.get("resources") == ["upstream.yaml"]
    patches = kust.get("patches", [])
    assert any(p.get("path") == "patch-kind.yaml" for p in patches)


def test_no_hpa_or_monitoring_in_metrics_server() -> None:
    docs = _load_all_yaml("upstream.yaml")
    for doc in docs:
        kind = doc.get("kind", "")
        assert kind != "HorizontalPodAutoscaler", "HPA must not exist in metrics-server manifests"
        assert "ServiceMonitor" not in kind, "Prometheus ServiceMonitor must not exist"
        assert "PrometheusRule" not in kind, "PrometheusRule must not exist"
        assert kind != "Secret", "No Secrets should be created in metrics-server manifests"


def test_no_external_service_exposure() -> None:
    docs = _load_all_yaml("upstream.yaml")
    for doc in docs:
        if doc.get("kind") == "Service":
            spec = doc.get("spec", {})
            svc_type = spec.get("type", "ClusterIP")
            assert svc_type == "ClusterIP", "Metrics Server service must be ClusterIP"
        if doc.get("kind") == "Deployment":
            pod_spec = doc.get("spec", {}).get("template", {}).get("spec", {})
            assert not pod_spec.get("hostNetwork", False), "Metrics server must not use hostNetwork"


def test_rbac_not_broadened() -> None:
    docs = _load_all_yaml("upstream.yaml")
    cluster_roles = [d for d in docs if d.get("kind") == "ClusterRole"]
    for cr in cluster_roles:
        name = cr.get("metadata", {}).get("name", "")
        for rule in cr.get("rules", []):
            verbs = rule.get("verbs", [])
            # Must not grant cluster-admin wildcard permissions
            assert "*" not in verbs, f"ClusterRole {name} must not grant wildcard verbs"
            resources = rule.get("resources", [])
            assert "*" not in resources, f"ClusterRole {name} must not grant wildcard resources"


def test_removal_script_safety() -> None:
    remove_script = SCRIPTS_DIR / "remove-metrics-server.ps1"
    assert remove_script.is_file(), "remove-metrics-server.ps1 must exist"
    content = remove_script.read_text(encoding="utf-8")
    assert "ConfirmMetricsServerRemoval" in content
    assert "kind-kubepredict-dev" in content
    assert "docker system prune" not in content
    assert "docker image prune" not in content
