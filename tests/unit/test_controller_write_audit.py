"""Unit tests for static write-capability audit (Phase 9A)."""

from pathlib import Path

from controller.audit import (
    audit_powershell_file,
    audit_python_file,
    run_write_capability_audit,
)


class TestWriteCapabilityAudit:
    """Validate static write-capability audit precision and violation detection."""

    def test_clean_controller_codebase_passes_audit(self) -> None:
        report = run_write_capability_audit()
        assert report.passed is True
        assert len(report.violations) == 0
        assert len(report.files_scanned) > 0

    def test_flags_forbidden_kubernetes_import(self, tmp_path: Path) -> None:
        bad_code = """
import kubernetes
from kubernetes import client

def get_k8s():
    return client.AppsV1Api()
"""
        bad_file = tmp_path / "bad_import.py"
        bad_file.write_text(bad_code, encoding="utf-8")

        violations = audit_python_file(bad_file)
        assert len(violations) >= 2
        rules = [v.rule for v in violations]
        assert "FORBIDDEN_K8S_IMPORT" in rules

    def test_flags_forbidden_patch_scale_call(self, tmp_path: Path) -> None:
        bad_code = """
def scale_workload(api_client, name, ns, replicas):
    api_client.patch_namespaced_deployment_scale(name, ns, body={"spec": {"replicas": replicas}})
"""
        bad_file = tmp_path / "bad_call.py"
        bad_file.write_text(bad_code, encoding="utf-8")

        violations = audit_python_file(bad_file)
        assert len(violations) >= 1
        assert violations[0].rule == "FORBIDDEN_WRITE_CALL"
        assert "patch_namespaced_deployment_scale" in violations[0].explanation

    def test_flags_forbidden_kubectl_write_in_powershell(self, tmp_path: Path) -> None:
        bad_ps = """
# Safe read-only check
kubectl get pods -n default

# Forbidden write mutation
kubectl scale deployment my-app --replicas=3
"""
        bad_file = tmp_path / "bad_script.ps1"
        bad_file.write_text(bad_ps, encoding="utf-8")

        violations = audit_powershell_file(bad_file)
        assert len(violations) == 1
        assert violations[0].rule == "FORBIDDEN_KUBECTL_WRITE"
        assert "kubectl scale" in violations[0].explanation

    def test_ignores_comments_and_docstrings(self, tmp_path: Path) -> None:
        safe_code = '''
"""This module does not call patch or scale on deployment.
It avoids kubectl apply and kubectl delete.
"""

def safe_func() -> str:
    # We never scale or patch anything here
    return "observe"
'''
        safe_file = tmp_path / "safe_comments.py"
        safe_file.write_text(safe_code, encoding="utf-8")

        violations = audit_python_file(safe_file)
        assert len(violations) == 0

        safe_ps = """
<#
Do not use kubectl scale or kubectl delete here.
#>
# Another comment about kubectl apply
kubectl get pods
"""
        safe_ps_file = tmp_path / "safe_comments.ps1"
        safe_ps_file.write_text(safe_ps, encoding="utf-8")

        ps_violations = audit_powershell_file(safe_ps_file)
        assert len(ps_violations) == 0
