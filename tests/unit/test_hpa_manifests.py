from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
HPA_DIR = REPO_ROOT / "kubernetes" / "hpa"
SCRIPTS_DIR = REPO_ROOT / "scripts" / "kubernetes"


def _load_hpa_manifest() -> list[dict[str, Any]]:
    file_path = HPA_DIR / "hpa.yaml"
    assert file_path.exists(), f"File hpa.yaml does not exist in {HPA_DIR}"
    with open(file_path, "r", encoding="utf-8") as f:
        docs = list(yaml.safe_load_all(f))
        return [d for d in docs if d is not None]


def test_hpa_directory_structure() -> None:
    assert HPA_DIR.is_dir(), "kubernetes/hpa directory must exist"
    assert (HPA_DIR / "hpa.yaml").is_file(), "hpa.yaml must exist"
    assert (HPA_DIR / "kustomization.yaml").is_file(), "kustomization.yaml must exist"
    assert not (HPA_DIR / ".gitkeep").exists(), ".gitkeep should be removed once real files exist"


def test_hpa_manifest_parses_and_schema() -> None:
    docs = _load_hpa_manifest()
    assert len(docs) == 1, "hpa.yaml must contain exactly one document"
    hpa = docs[0]

    assert hpa.get("apiVersion") == "autoscaling/v2"
    assert hpa.get("kind") == "HorizontalPodAutoscaler"
    assert hpa.get("metadata", {}).get("name") == "kubepredict-workload-api"
    assert hpa.get("metadata", {}).get("namespace") == "kubepredict-system"

    scale_target = hpa.get("spec", {}).get("scaleTargetRef", {})
    assert scale_target.get("apiVersion") == "apps/v1"
    assert scale_target.get("kind") == "Deployment"
    assert scale_target.get("name") == "kubepredict-workload-api"


def test_hpa_replica_bounds() -> None:
    docs = _load_hpa_manifest()
    spec = docs[0].get("spec", {})
    min_replicas = spec.get("minReplicas")
    max_replicas = spec.get("maxReplicas")

    assert min_replicas == 1, "minReplicas must equal 1"
    assert max_replicas == 6, "maxReplicas must equal 6"
    assert min_replicas < max_replicas, "minReplicas must be strictly less than maxReplicas"
    assert min_replicas > 0, "Scale-to-zero is not supported; minReplicas must be > 0"


def test_hpa_metrics_specification() -> None:
    docs = _load_hpa_manifest()
    metrics = docs[0].get("spec", {}).get("metrics", [])
    assert len(metrics) == 1, "HPA baseline must configure exactly one metric (CPU)"

    metric = metrics[0]
    assert metric.get("type") == "Resource"
    resource = metric.get("resource", {})
    assert resource.get("name") == "cpu"

    target = resource.get("target", {})
    assert target.get("type") == "Utilization"
    assert target.get("averageUtilization") == 60


def test_no_forbidden_metrics() -> None:
    docs = _load_hpa_manifest()
    metrics = docs[0].get("spec", {}).get("metrics", [])

    for m in metrics:
        m_type = m.get("type")
        assert m_type not in ["Pods", "Object", "External"], f"Forbidden metric type: {m_type}"
        if m_type == "Resource":
            res_name = m.get("resource", {}).get("name")
            assert res_name != "memory", "Memory metric is forbidden in CPU HPA baseline"
            assert res_name == "cpu", f"Only CPU resource metric is permitted, got: {res_name}"


def test_hpa_behavior_policies() -> None:
    docs = _load_hpa_manifest()
    behavior = docs[0].get("spec", {}).get("behavior", {})
    assert behavior, "behavior section must be explicitly configured"

    scale_up = behavior.get("scaleUp", {})
    assert scale_up.get("stabilizationWindowSeconds") == 0
    assert scale_up.get("selectPolicy") == "Max"
    scale_up_policies = scale_up.get("policies", [])
    assert len(scale_up_policies) == 2

    pods_policy = next(p for p in scale_up_policies if p.get("type") == "Pods")
    assert pods_policy.get("value") == 2
    assert pods_policy.get("periodSeconds") == 60

    percent_policy = next(p for p in scale_up_policies if p.get("type") == "Percent")
    assert percent_policy.get("value") == 100
    assert percent_policy.get("periodSeconds") == 60

    scale_down = behavior.get("scaleDown", {})
    assert scale_down.get("stabilizationWindowSeconds") == 300
    assert scale_down.get("selectPolicy") == "Max"
    scale_down_policies = scale_down.get("policies", [])
    assert len(scale_down_policies) == 2

    down_pods_policy = next(p for p in scale_down_policies if p.get("type") == "Pods")
    assert down_pods_policy.get("value") == 1
    assert down_pods_policy.get("periodSeconds") == 60

    down_percent_policy = next(p for p in scale_down_policies if p.get("type") == "Percent")
    assert down_percent_policy.get("value") == 50
    assert down_percent_policy.get("periodSeconds") == 60


def test_kustomization_parses_and_clean() -> None:
    kust_path = HPA_DIR / "kustomization.yaml"
    with open(kust_path, "r", encoding="utf-8") as f:
        kust = yaml.safe_load(f)

    assert kust.get("kind") == "Kustomization"
    assert kust.get("resources") == ["hpa.yaml"]
    assert "patches" not in kust or len(kust.get("patches", [])) == 0
    assert "generators" not in kust


def test_no_extra_resources_in_hpa_directory() -> None:
    docs = _load_hpa_manifest()
    kinds = [doc.get("kind") for doc in docs]
    forbidden_kinds = [
        "Deployment",
        "Service",
        "ConfigMap",
        "Secret",
        "APIService",
        "ServiceAccount",
        "ClusterRole",
        "ClusterRoleBinding",
        "Role",
        "RoleBinding",
        "ServiceMonitor",
        "PrometheusRule",
    ]
    for fk in forbidden_kinds:
        assert fk not in kinds, f"Forbidden kind {fk} found in HPA manifest"


def test_hpa_scripts_exist_and_safe() -> None:
    deploy_script = SCRIPTS_DIR / "deploy-hpa.ps1"
    inspect_script = SCRIPTS_DIR / "inspect-hpa.ps1"
    remove_script = SCRIPTS_DIR / "remove-hpa.ps1"

    assert deploy_script.is_file(), "deploy-hpa.ps1 must exist"
    assert inspect_script.is_file(), "inspect-hpa.ps1 must exist"
    assert remove_script.is_file(), "remove-hpa.ps1 must exist"

    deploy_content = deploy_script.read_text(encoding="utf-8")
    assert "kind-kubepredict-dev" in deploy_content
    assert "kubepredict-system" in deploy_content
    assert "v1beta1.metrics.k8s.io" in deploy_content
    assert "kubectl kustomize" in deploy_content

    inspect_content = inspect_script.read_text(encoding="utf-8")
    assert "kind-kubepredict-dev" in inspect_content
    assert "kubepredict-system" in inspect_content

    remove_content = remove_script.read_text(encoding="utf-8")
    assert "ConfirmHpaRemoval" in remove_content
    assert "kind-kubepredict-dev" in remove_content
    assert "docker system prune" not in remove_content
    assert "docker image prune" not in remove_content
