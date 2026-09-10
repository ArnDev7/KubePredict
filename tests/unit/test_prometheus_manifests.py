from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
PROMETHEUS_DIR = REPO_ROOT / "kubernetes" / "monitoring" / "prometheus"
SCRIPTS_DIR = REPO_ROOT / "scripts" / "kubernetes"


def _load_yaml(filename: str) -> list[dict[str, Any]]:
    file_path = PROMETHEUS_DIR / filename
    assert file_path.exists(), f"File {filename} does not exist in {PROMETHEUS_DIR}"
    with open(file_path, "r", encoding="utf-8") as f:
        docs = list(yaml.safe_load_all(f))
        return [d for d in docs if d is not None]


def test_prometheus_directory_and_files_exist() -> None:
    assert PROMETHEUS_DIR.is_dir(), "kubernetes/monitoring/prometheus directory must exist"
    required_files = [
        "namespace.yaml",
        "serviceaccount.yaml",
        "clusterrole.yaml",
        "clusterrolebinding.yaml",
        "configmap.yaml",
        "deployment.yaml",
        "service.yaml",
        "kustomization.yaml",
    ]
    for rf in required_files:
        assert (PROMETHEUS_DIR / rf).is_file(), f"{rf} must exist in {PROMETHEUS_DIR}"


def test_prometheus_kustomization_resources() -> None:
    docs = _load_yaml("kustomization.yaml")
    assert len(docs) == 1
    kust = docs[0]
    expected_resources = [
        "namespace.yaml",
        "serviceaccount.yaml",
        "clusterrole.yaml",
        "clusterrolebinding.yaml",
        "configmap.yaml",
        "deployment.yaml",
        "service.yaml",
    ]
    resources = kust.get("resources", [])
    assert sorted(resources) == sorted(expected_resources)


def test_prometheus_namespace() -> None:
    docs = _load_yaml("namespace.yaml")
    assert len(docs) == 1
    ns = docs[0]
    assert ns.get("apiVersion") == "v1"
    assert ns.get("kind") == "Namespace"
    assert ns.get("metadata", {}).get("name") == "kubepredict-monitoring"


def test_prometheus_serviceaccount() -> None:
    docs = _load_yaml("serviceaccount.yaml")
    assert len(docs) == 1
    sa = docs[0]
    assert sa.get("apiVersion") == "v1"
    assert sa.get("kind") == "ServiceAccount"
    assert sa.get("metadata", {}).get("name") == "kubepredict-prometheus"
    assert sa.get("metadata", {}).get("namespace") == "kubepredict-monitoring"
    assert sa.get("automountServiceAccountToken") is True


def test_prometheus_rbac_rules_and_least_privilege() -> None:
    cr_docs = _load_yaml("clusterrole.yaml")
    assert len(cr_docs) == 1
    cr = cr_docs[0]
    assert cr.get("apiVersion") == "rbac.authorization.k8s.io/v1"
    assert cr.get("kind") == "ClusterRole"
    assert cr.get("metadata", {}).get("name") == "kubepredict-prometheus-discovery"

    disallowed_verbs = {
        "create",
        "update",
        "patch",
        "delete",
        "deletecollection",
        "impersonate",
        "escalate",
        "bind",
        "*",
    }
    permitted_verbs = {"get", "list", "watch"}

    rules = cr.get("rules", [])
    assert len(rules) > 0, "ClusterRole must define rules"

    for rule in rules:
        verbs = set(rule.get("verbs", []))
        resources = set(rule.get("resources", []))

        assert not (verbs & disallowed_verbs), f"Disallowed verbs found in ClusterRole: {verbs}"
        assert verbs.issubset(permitted_verbs), f"Verbs must only be get/list/watch, found: {verbs}"
        assert "*" not in resources, "Wildcard resources forbidden in ClusterRole"
        assert "secrets" not in resources, "Secrets access forbidden in ClusterRole"

    crb_docs = _load_yaml("clusterrolebinding.yaml")
    assert len(crb_docs) == 1
    crb = crb_docs[0]
    assert crb.get("apiVersion") == "rbac.authorization.k8s.io/v1"
    assert crb.get("kind") == "ClusterRoleBinding"
    assert crb.get("metadata", {}).get("name") == "kubepredict-prometheus-discovery"
    assert crb.get("roleRef", {}).get("name") == "kubepredict-prometheus-discovery"
    subjects = crb.get("subjects", [])
    assert len(subjects) == 1
    assert subjects[0].get("name") == "kubepredict-prometheus"
    assert subjects[0].get("namespace") == "kubepredict-monitoring"


def test_prometheus_configmap_and_scrape_jobs() -> None:
    docs = _load_yaml("configmap.yaml")
    assert len(docs) == 1
    cm = docs[0]
    assert cm.get("apiVersion") == "v1"
    assert cm.get("kind") == "ConfigMap"
    assert cm.get("metadata", {}).get("name") == "kubepredict-prometheus-config"
    assert cm.get("metadata", {}).get("namespace") == "kubepredict-monitoring"

    prom_yml_str = cm.get("data", {}).get("prometheus.yml", "")
    assert prom_yml_str, "prometheus.yml must be present in ConfigMap data"

    prom_cfg = yaml.safe_load(prom_yml_str)
    global_cfg = prom_cfg.get("global", {})
    assert global_cfg.get("scrape_interval") == "15s"
    assert global_cfg.get("evaluation_interval") == "15s"

    assert "remote_write" not in prom_cfg, "remote_write must not be configured"
    assert "alerting" not in prom_cfg, "alerting/Alertmanager must not be configured"

    scrape_configs = prom_cfg.get("scrape_configs", [])
    job_names = [sc.get("job_name") for sc in scrape_configs]
    assert "prometheus" in job_names, "Prometheus self-monitoring job required"
    assert "kubepredict-workload" in job_names, "KubePredict workload scrape job required"

    # Verify workload job constraints
    workload_job = next(sc for sc in scrape_configs if sc.get("job_name") == "kubepredict-workload")
    sd_configs = workload_job.get("kubernetes_sd_configs", [])
    assert len(sd_configs) == 1
    sd = sd_configs[0]
    assert sd.get("role") == "pod"
    assert sd.get("namespaces", {}).get("names") == ["kubepredict-system"]

    relabel_configs = workload_job.get("relabel_configs", [])
    # Verify relabeling ensures low-cardinality and constrained targeting
    kept_phases = [
        rc.get("regex")
        for rc in relabel_configs
        if rc.get("action") == "keep" and rc.get("source_labels") == ["__meta_kubernetes_pod_phase"]
    ]
    assert "Running" in kept_phases


def test_prometheus_deployment_specs_and_security() -> None:
    docs = _load_yaml("deployment.yaml")
    assert len(docs) == 1
    dep = docs[0]
    assert dep.get("apiVersion") == "apps/v1"
    assert dep.get("kind") == "Deployment"
    assert dep.get("metadata", {}).get("name") == "kubepredict-prometheus"
    assert dep.get("metadata", {}).get("namespace") == "kubepredict-monitoring"

    spec = dep.get("spec", {})
    assert spec.get("replicas") == 1

    template = spec.get("template", {})
    pod_spec = template.get("spec", {})
    assert pod_spec.get("serviceAccountName") == "kubepredict-prometheus"
    assert pod_spec.get("automountServiceAccountToken") is True

    pod_sec = pod_spec.get("securityContext", {})
    assert pod_sec.get("runAsNonRoot") is True
    assert pod_sec.get("seccompProfile", {}).get("type") == "RuntimeDefault"

    containers = pod_spec.get("containers", [])
    assert len(containers) == 1
    c = containers[0]
    assert c.get("name") == "prometheus"

    image = c.get("image", "")
    assert image.startswith("prom/prometheus:v")
    assert not image.endswith(":latest")
    assert ":" in image and "@" not in image

    ports = c.get("ports", [])
    assert any(p.get("containerPort") == 9090 and p.get("name") == "http" for p in ports)

    # Probes
    assert c.get("startupProbe", {}).get("httpGet", {}).get("path") == "/-/healthy"
    assert c.get("livenessProbe", {}).get("httpGet", {}).get("path") == "/-/healthy"
    assert c.get("readinessProbe", {}).get("httpGet", {}).get("path") == "/-/ready"

    # Container security
    c_sec = c.get("securityContext", {})
    assert c_sec.get("allowPrivilegeEscalation") is False
    assert c_sec.get("readOnlyRootFilesystem") is True
    assert c_sec.get("runAsNonRoot") is True
    assert c_sec.get("capabilities", {}).get("drop") == ["ALL"]
    assert c_sec.get("privileged") in (False, None)

    # Resources
    resources = c.get("resources", {})
    assert resources.get("requests", {}).get("cpu") == "100m"
    assert resources.get("requests", {}).get("memory") == "256Mi"
    assert resources.get("limits", {}).get("cpu") == "500m"
    assert resources.get("limits", {}).get("memory") == "512Mi"

    # Storage and volumes
    volumes = pod_spec.get("volumes", [])
    storage_vol = next((v for v in volumes if v.get("name") == "storage"), None)
    assert storage_vol is not None
    assert "emptyDir" in storage_vol
    assert storage_vol.get("emptyDir", {}).get("sizeLimit") == "1Gi"
    assert "hostPath" not in str(volumes)
    assert "persistentVolumeClaim" not in str(volumes)


def test_prometheus_service() -> None:
    docs = _load_yaml("service.yaml")
    assert len(docs) == 1
    svc = docs[0]
    assert svc.get("apiVersion") == "v1"
    assert svc.get("kind") == "Service"
    assert svc.get("metadata", {}).get("name") == "kubepredict-prometheus"
    assert svc.get("metadata", {}).get("namespace") == "kubepredict-monitoring"

    spec = svc.get("spec", {})
    assert spec.get("type") == "ClusterIP"
    ports = spec.get("ports", [])
    assert any(p.get("port") == 9090 and p.get("targetPort") in (9090, "http") for p in ports)


def test_prohibited_resources_absent() -> None:
    # Ensure no Grafana, HPA, Alertmanager, or Prometheus Operator CRDs are in the monitoring manifests
    for yml_file in PROMETHEUS_DIR.glob("*.yaml"):
        with open(yml_file, "r", encoding="utf-8") as f:
            content = f.read().lower()
            assert "grafana" not in content, f"Grafana reference found in {yml_file.name}"
            assert "servicemonitor" not in content, f"ServiceMonitor found in {yml_file.name}"
            assert "podmonitor" not in content, f"PodMonitor found in {yml_file.name}"
            assert "alertmanager" not in content, f"Alertmanager found in {yml_file.name}"
            if yml_file.name != "hpa.yaml":
                assert "horizontalpodautoscaler" not in content, f"HPA found in {yml_file.name}"
