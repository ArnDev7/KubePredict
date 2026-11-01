"""Unit tests for Phase 11 controller Kubernetes manifests and RBAC configuration."""

from pathlib import Path

import yaml

CONTROLLER_MANIFESTS_DIR = Path("kubernetes/controller")


class TestControllerManifests:
    """Validate safety, least-privilege RBAC, and container configuration in manifests."""

    def test_all_expected_manifests_exist(self) -> None:
        expected = [
            "serviceaccount.yaml",
            "role.yaml",
            "rolebinding.yaml",
            "configmap.yaml",
            "deployment.yaml",
            "service.yaml",
            "kustomization.yaml",
        ]
        for fname in expected:
            fpath = CONTROLLER_MANIFESTS_DIR / fname
            assert fpath.exists(), f"Missing required manifest: {fname}"

    def test_serviceaccount_manifest(self) -> None:
        data = yaml.safe_load(
            (CONTROLLER_MANIFESTS_DIR / "serviceaccount.yaml").read_text(encoding="utf-8")
        )
        assert data["kind"] == "ServiceAccount"
        assert data["metadata"]["name"] == "kubepredict-controller"
        assert data["metadata"]["namespace"] == "kubepredict-system"

    def test_role_least_privilege(self) -> None:
        data = yaml.safe_load((CONTROLLER_MANIFESTS_DIR / "role.yaml").read_text(encoding="utf-8"))
        assert data["kind"] == "Role", "Must be namespace-scoped Role, not ClusterRole"
        assert data["metadata"]["name"] == "kubepredict-controller-scale"
        assert data["metadata"]["namespace"] == "kubepredict-system"

        rules = data.get("rules", [])
        assert len(rules) >= 1

        for rule in rules:
            # Forbid wildcard verbs and resources
            assert "*" not in rule.get("verbs", [])
            assert "*" not in rule.get("resources", [])
            assert "*" not in rule.get("apiGroups", [])

            # Forbid secrets, pods, hpas, configmaps
            resources = rule.get("resources", [])
            assert "secrets" not in resources
            assert "pods" not in resources
            assert "horizontalpodautoscalers" not in resources
            assert "configmaps" not in resources

            # Verify resourceNames restricts target
            resource_names = rule.get("resourceNames", [])
            assert resource_names == ["kubepredict-workload-api"], (
                "Must hard-restrict to kubepredict-workload-api"
            )

            # Check verbs
            verbs = rule.get("verbs", [])
            for v in verbs:
                assert v in ("get", "patch"), f"Unexpected verb in least-privilege role: {v}"

    def test_rolebinding_least_privilege(self) -> None:
        data = yaml.safe_load(
            (CONTROLLER_MANIFESTS_DIR / "rolebinding.yaml").read_text(encoding="utf-8")
        )
        assert data["kind"] == "RoleBinding", (
            "Must be namespace-scoped RoleBinding, not ClusterRoleBinding"
        )
        assert data["metadata"]["name"] == "kubepredict-controller-scale"
        assert data["metadata"]["namespace"] == "kubepredict-system"

        role_ref = data.get("roleRef", {})
        assert role_ref.get("kind") == "Role"
        assert role_ref.get("name") == "kubepredict-controller-scale"

        subjects = data.get("subjects", [])
        assert len(subjects) == 1
        sub = subjects[0]
        assert sub.get("kind") == "ServiceAccount"
        assert sub.get("name") == "kubepredict-controller"
        assert sub.get("namespace") == "kubepredict-system"

    def test_configmap_committed_safe_defaults(self) -> None:
        data = yaml.safe_load(
            (CONTROLLER_MANIFESTS_DIR / "configmap.yaml").read_text(encoding="utf-8")
        )
        assert data["kind"] == "ConfigMap"
        assert data["metadata"]["name"] == "kubepredict-controller-config"

        cfg_data = data.get("data", {})
        assert cfg_data.get("CONTROLLER_MODE") == "dry_run", "Committed default must be dry_run"
        assert cfg_data.get("ENABLE_LIVE_SCALING") == "false", "Committed default must be false"

    def test_deployment_security_and_specs(self) -> None:
        data = yaml.safe_load(
            (CONTROLLER_MANIFESTS_DIR / "deployment.yaml").read_text(encoding="utf-8")
        )
        assert data["kind"] == "Deployment"
        assert data["metadata"]["name"] == "kubepredict-controller"
        assert data["metadata"]["namespace"] == "kubepredict-system"

        spec = data["spec"]
        assert spec["replicas"] == 1, "Controller replicas must strictly equal 1"
        assert spec["strategy"]["type"] == "Recreate", (
            "Deployment strategy must be Recreate to prevent dual writers"
        )

        pod_spec = spec["template"]["spec"]
        assert pod_spec["serviceAccountName"] == "kubepredict-controller"

        # Pod security context
        pod_sc = pod_spec.get("securityContext", {})
        assert pod_sc.get("runAsNonRoot") is True
        assert pod_sc.get("runAsUser") == 1000
        assert pod_sc.get("runAsGroup") == 1000
        assert pod_sc.get("seccompProfile", {}).get("type") == "RuntimeDefault"

        # Container spec
        containers = pod_spec["containers"]
        assert len(containers) == 1
        c = containers[0]
        assert c["name"] == "controller"
        assert c["image"] == "kubepredict-controller:0.1.0"
        assert "latest" not in c["image"]

        # Container security context
        c_sc = c.get("securityContext", {})
        assert c_sc.get("allowPrivilegeEscalation") is False
        assert c_sc.get("readOnlyRootFilesystem") is True
        assert "ALL" in c_sc.get("capabilities", {}).get("drop", [])

        # Resources
        res = c.get("resources", {})
        assert res.get("requests", {}).get("cpu") == "100m"
        assert res.get("requests", {}).get("memory") == "128Mi"
        assert res.get("limits", {}).get("cpu") == "500m"
        assert res.get("limits", {}).get("memory") == "384Mi"

        # Probes
        assert c.get("livenessProbe", {}).get("httpGet", {}).get("path") == "/healthz"
        assert c.get("readinessProbe", {}).get("httpGet", {}).get("path") == "/readyz"

    def test_service_is_cluster_ip(self) -> None:
        data = yaml.safe_load(
            (CONTROLLER_MANIFESTS_DIR / "service.yaml").read_text(encoding="utf-8")
        )
        assert data["kind"] == "Service"
        assert data["metadata"]["name"] == "kubepredict-controller"
        assert data["spec"].get("type") == "ClusterIP", "Must be internal ClusterIP only"
        ports = data["spec"].get("ports", [])
        assert any(p.get("port") == 9101 for p in ports)
