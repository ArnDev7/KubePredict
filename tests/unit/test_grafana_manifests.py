from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
GRAFANA_DIR = REPO_ROOT / "kubernetes" / "monitoring" / "grafana"
SCRIPTS_DIR = REPO_ROOT / "scripts" / "kubernetes"


def _load_yaml(filename: str) -> list[dict[str, Any]]:
    file_path = GRAFANA_DIR / filename
    assert file_path.exists(), f"File {filename} does not exist in {GRAFANA_DIR}"
    with open(file_path, "r", encoding="utf-8") as f:
        docs = list(yaml.safe_load_all(f))
        return [d for d in docs if d is not None]


def test_grafana_directory_and_files_exist() -> None:
    assert GRAFANA_DIR.is_dir(), "kubernetes/monitoring/grafana directory must exist"
    required_files = [
        "configmap-datasource.yaml",
        "configmap-dashboard-provider.yaml",
        "configmap-dashboard.yaml",
        "deployment.yaml",
        "service.yaml",
        "kustomization.yaml",
    ]
    for rf in required_files:
        assert (GRAFANA_DIR / rf).is_file(), f"{rf} must exist in {GRAFANA_DIR}"


def test_grafana_no_committed_secret_manifest() -> None:
    for f in GRAFANA_DIR.iterdir():
        assert "secret" not in f.name.lower(), f"No secret manifest should be committed: {f.name}"
        assert "credential" not in f.name.lower(), (
            f"No credential manifest should be committed: {f.name}"
        )


def test_grafana_kustomization_resources() -> None:
    docs = _load_yaml("kustomization.yaml")
    assert len(docs) == 1
    kust = docs[0]
    expected_resources = [
        "configmap-datasource.yaml",
        "configmap-dashboard-provider.yaml",
        "configmap-dashboard.yaml",
        "deployment.yaml",
        "service.yaml",
    ]
    resources = kust.get("resources", [])
    assert sorted(resources) == sorted(expected_resources)
    # Ensure no forbidden resources are referenced
    for r in resources:
        assert "secret" not in r.lower()
        assert "prometheus" not in r.lower()
        assert "hpa" not in r.lower()
        assert "metrics" not in r.lower()


def test_grafana_datasource_configmap() -> None:
    docs = _load_yaml("configmap-datasource.yaml")
    assert len(docs) == 1
    cm = docs[0]
    assert cm.get("apiVersion") == "v1"
    assert cm.get("kind") == "ConfigMap"
    assert cm.get("metadata", {}).get("name") == "kubepredict-grafana-datasource"
    assert cm.get("metadata", {}).get("namespace") == "kubepredict-monitoring"

    data = cm.get("data", {})
    assert "datasource.yaml" in data
    ds_parsed = yaml.safe_load(data["datasource.yaml"])
    datasources = ds_parsed.get("datasources", [])
    assert len(datasources) == 1
    ds = datasources[0]
    assert ds.get("name") == "KubePredict Prometheus"
    assert ds.get("type") == "prometheus"
    assert ds.get("uid") == "kubepredict-prometheus"
    assert ds.get("access") == "proxy"
    assert ds.get("isDefault") is True
    assert ds.get("editable") is False
    assert (
        ds.get("url")
        == "http://kubepredict-prometheus.kubepredict-monitoring.svc.cluster.local:9090"
    )
    # Ensure no credentials or localhost in datasource config
    assert "password" not in data["datasource.yaml"].lower()
    assert "localhost" not in ds.get("url", "").lower()
    assert "127.0.0.1" not in ds.get("url", "")


def test_grafana_dashboard_provider_configmap() -> None:
    docs = _load_yaml("configmap-dashboard-provider.yaml")
    assert len(docs) == 1
    cm = docs[0]
    assert cm.get("apiVersion") == "v1"
    assert cm.get("kind") == "ConfigMap"
    assert cm.get("metadata", {}).get("name") == "kubepredict-grafana-dashboard-provider"
    assert cm.get("metadata", {}).get("namespace") == "kubepredict-monitoring"

    data = cm.get("data", {})
    assert "dashboards.yaml" in data
    provider_parsed = yaml.safe_load(data["dashboards.yaml"])
    providers = provider_parsed.get("providers", [])
    assert len(providers) == 1
    prov = providers[0]
    assert prov.get("name") == "KubePredict"
    assert prov.get("folder") == "KubePredict"
    assert prov.get("type") == "file"
    assert prov.get("disableDeletion") is True
    assert prov.get("editable") is False
    assert prov.get("options", {}).get("path") == "/var/lib/grafana/dashboards/kubepredict"


def test_grafana_deployment_structure_and_safety() -> None:
    docs = _load_yaml("deployment.yaml")
    assert len(docs) == 1
    dep = docs[0]
    assert dep.get("apiVersion") == "apps/v1"
    assert dep.get("kind") == "Deployment"
    assert dep.get("metadata", {}).get("name") == "kubepredict-grafana"
    assert dep.get("metadata", {}).get("namespace") == "kubepredict-monitoring"
    assert dep.get("spec", {}).get("replicas") == 1

    spec = dep.get("spec", {})
    template_spec = spec.get("template", {}).get("spec", {})

    # Pod level security
    pod_sec = template_spec.get("securityContext", {})
    assert pod_sec.get("runAsNonRoot") is True
    assert pod_sec.get("seccompProfile", {}).get("type") == "RuntimeDefault"

    # Automount service account token should be false
    assert template_spec.get("automountServiceAccountToken") is False

    # Container checks
    containers = template_spec.get("containers", [])
    assert len(containers) == 1
    c = containers[0]
    assert c.get("name") == "grafana"

    image = c.get("image", "")
    assert image.startswith("grafana/grafana:")
    assert not image.endswith(":latest")
    assert not image.endswith(":main")
    assert not image.endswith(":master")

    # Ports
    ports = c.get("ports", [])
    assert len(ports) == 1
    assert ports[0].get("containerPort") == 3000
    assert ports[0].get("name") == "http"

    # Container securityContext
    c_sec = c.get("securityContext", {})
    assert c_sec.get("allowPrivilegeEscalation") is False
    assert c_sec.get("runAsNonRoot") is True
    assert c_sec.get("readOnlyRootFilesystem") is True
    assert c_sec.get("capabilities", {}).get("drop") == ["ALL"]
    assert c_sec.get("privileged", False) is False

    # Resources
    res = c.get("resources", {})
    assert res.get("requests", {}).get("cpu") == "100m"
    assert res.get("requests", {}).get("memory") == "128Mi"
    assert res.get("limits", {}).get("cpu") == "500m"
    assert res.get("limits", {}).get("memory") == "384Mi"

    # Probes
    for probe_name in ["startupProbe", "livenessProbe", "readinessProbe"]:
        probe = c.get(probe_name, {})
        assert probe.get("httpGet", {}).get("path") == "/api/health"
        assert probe.get("httpGet", {}).get("port") == "http"

    # Environment variables
    env_vars = {e.get("name"): e for e in c.get("env", [])}
    assert env_vars.get("GF_SERVER_HTTP_PORT", {}).get("value") == "3000"
    assert env_vars.get("GF_USERS_ALLOW_SIGN_UP", {}).get("value") == "false"
    assert env_vars.get("GF_AUTH_ANONYMOUS_ENABLED", {}).get("value") == "false"
    assert env_vars.get("GF_SECURITY_DISABLE_GRAVATAR", {}).get("value") == "true"
    assert env_vars.get("GF_ANALYTICS_REPORTING_ENABLED", {}).get("value") == "false"
    assert env_vars.get("GF_ANALYTICS_CHECK_FOR_UPDATES", {}).get("value") == "false"
    assert env_vars.get("GF_UPDATE_CHECK_ENABLED", {}).get("value") == "false"

    # Admin credentials must reference Secret
    admin_pw_env = env_vars.get("GF_SECURITY_ADMIN_PASSWORD", {})
    assert "value" not in admin_pw_env, "Admin password must not be in plain environment value"
    secret_ref = admin_pw_env.get("valueFrom", {}).get("secretKeyRef", {})
    assert secret_ref.get("name") == "grafana-admin-credentials"
    assert secret_ref.get("key") == "admin-password"

    # Volumes check: no hostPath, no PVC, no docker socket, no kubeconfig
    volumes = template_spec.get("volumes", [])
    for v in volumes:
        assert "hostPath" not in v, "hostPath is strictly prohibited"
        assert "persistentVolumeClaim" not in v, "PVC is not permitted in Phase 6B"

    # Volume mounts: check readonly for provisioning
    mounts = {m.get("name"): m for m in c.get("volumeMounts", [])}
    assert mounts.get("datasource-config", {}).get("readOnly") is True
    assert mounts.get("dashboard-provider-config", {}).get("readOnly") is True
    assert mounts.get("dashboard-data", {}).get("readOnly") is True
    assert mounts.get("storage", {}).get("mountPath") == "/var/lib/grafana"
    assert mounts.get("tmp", {}).get("mountPath") == "/tmp"


def test_grafana_service_manifest() -> None:
    docs = _load_yaml("service.yaml")
    assert len(docs) == 1
    svc = docs[0]
    assert svc.get("apiVersion") == "v1"
    assert svc.get("kind") == "Service"
    assert svc.get("metadata", {}).get("name") == "kubepredict-grafana"
    assert svc.get("metadata", {}).get("namespace") == "kubepredict-monitoring"

    spec = svc.get("spec", {})
    assert spec.get("type") == "ClusterIP"
    ports = spec.get("ports", [])
    assert len(ports) == 1
    p = ports[0]
    assert p.get("port") == 3000
    assert p.get("targetPort") in ["http", 3000]
    assert p.get("protocol") == "TCP"

    # Confirm selector targets grafana deployment
    assert spec.get("selector", {}).get("app.kubernetes.io/component") == "monitoring-dashboard"


def test_grafana_scripts_exist() -> None:
    expected_scripts = [
        "deploy-grafana.ps1",
        "inspect-grafana.ps1",
        "port-forward-grafana.ps1",
        "remove-grafana.ps1",
    ]
    for s in expected_scripts:
        script_path = SCRIPTS_DIR / s
        assert script_path.is_file(), f"Script {s} must exist in {SCRIPTS_DIR}"
        content = script_path.read_text(encoding="utf-8")
        assert "kind-kubepredict-dev" in content, f"Script {s} must check exact context"
