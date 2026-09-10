import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
CANONICAL_JSON_PATH = (
    REPO_ROOT / "monitoring" / "grafana" / "dashboards" / "kubepredict-overview.json"
)
CONFIGMAP_YAML_PATH = (
    REPO_ROOT / "kubernetes" / "monitoring" / "grafana" / "configmap-dashboard.yaml"
)


def _load_canonical_dashboard() -> dict[str, Any]:
    assert CANONICAL_JSON_PATH.is_file(), (
        f"Canonical dashboard file not found at {CANONICAL_JSON_PATH}"
    )
    with open(CANONICAL_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_dashboard_json_metadata() -> None:
    dash = _load_canonical_dashboard()
    assert dash.get("uid") == "kubepredict-overview"
    assert dash.get("title") == "KubePredict Overview"
    assert dash.get("refresh") == "15s"
    assert dash.get("time", {}).get("from") == "now-15m"
    assert dash.get("time", {}).get("to") == "now"
    expected_tags = {"kubepredict", "kubernetes", "autoscaling", "prometheus"}
    assert expected_tags.issubset(set(dash.get("tags", [])))


def test_dashboard_panels_integrity_and_datasource() -> None:
    dash = _load_canonical_dashboard()
    panels = dash.get("panels", [])
    assert len(panels) == 18, f"Expected exactly 18 dashboard panels, found {len(panels)}"

    panel_ids: set[int] = set()
    required_apa_metrics = {
        "apa_http_requests_total",
        "apa_work_requests_total",
        "apa_http_request_duration_seconds",
        "apa_http_requests_in_progress",
        "apa_cpu_iterations_total",
        "apa_artificial_delay_seconds_total",
        "apa_work_duration_seconds",
    }
    found_apa_metrics: set[str] = set()

    for p in panels:
        pid = p.get("id")
        assert pid is not None
        assert pid not in panel_ids, f"Duplicate panel ID found: {pid}"
        panel_ids.add(pid)

        title = p.get("title", "")
        assert len(title.strip()) > 0, f"Panel {pid} has empty title"

        ptype = p.get("type")
        if ptype == "text":
            # Text panel checks
            content = p.get("options", {}).get("content", "")
            assert len(content.strip()) > 0
            assert "static" in content.lower() or "baseline" in content.lower()
            # Must not query live metrics
            assert "targets" not in p or len(p.get("targets", [])) == 0
        else:
            # Metric panel checks
            ds = p.get("datasource", {})
            assert isinstance(ds, dict), f"Panel {pid} datasource must be an object"
            assert ds.get("uid") == "kubepredict-prometheus"
            assert ds.get("type") == "prometheus"
            assert "id" not in ds or not isinstance(ds.get("id"), int), (
                f"Panel {pid} has numeric datasource ID"
            )

            targets = p.get("targets", [])
            assert len(targets) > 0, f"Metric panel {pid} must have targets"
            for t in targets:
                expr = t.get("expr", "")
                assert len(expr.strip()) > 0
                # Check that every target uses kubepredict-prometheus
                target_ds = t.get("datasource", {})
                assert target_ds.get("uid") == "kubepredict-prometheus"

                # Check metrics represented
                for m in required_apa_metrics:
                    if m in expr:
                        found_apa_metrics.add(m)

                # Ensure no forbidden high-cardinality or external labels
                assert "request_id" not in expr.lower()
                assert "url" not in expr.lower()
                assert "kube_pod_status" not in expr.lower()
                assert "kube_deployment" not in expr.lower()
                assert "localhost" not in expr.lower()
                assert "password" not in expr.lower()

    # All required apa_ metrics must be represented
    assert required_apa_metrics == found_apa_metrics, (
        f"Missing apa_ metrics in dashboard: {required_apa_metrics - found_apa_metrics}"
    )


def test_dashboard_promql_query_conventions() -> None:
    dash = _load_canonical_dashboard()
    panels = {p.get("id"): p for p in dash.get("panels", [])}

    # Panel 5: p95 HTTP duration
    p5_expr = panels[5]["targets"][0]["expr"]
    assert "histogram_quantile(0.95" in p5_expr
    assert "sum by (le)" in p5_expr

    # Panel 11: p95 Work duration
    p11_expr = panels[11]["targets"][0]["expr"]
    assert "histogram_quantile(0.95" in p11_expr
    assert "sum by (le)" in p11_expr

    # Panel 13: HTTP request rate by pod
    p13_expr = panels[13]["targets"][0]["expr"]
    assert "sum by (pod)" in p13_expr

    # Panel 14: Work request rate by pod
    p14_expr = panels[14]["targets"][0]["expr"]
    assert "sum by (pod)" in p14_expr

    # Panel 15: p95 request duration by pod
    p15_expr = panels[15]["targets"][0]["expr"]
    assert "histogram_quantile(0.95" in p15_expr
    assert "by (pod, le)" in p15_expr or "by (le, pod)" in p15_expr

    # Panel 16: Scrape status by pod
    p16_expr = panels[16]["targets"][0]["expr"]
    assert 'up{job="kubepredict-workload"}' in p16_expr


def test_dashboard_configmap_matches_canonical_json() -> None:
    assert CONFIGMAP_YAML_PATH.is_file(), f"ConfigMap file not found at {CONFIGMAP_YAML_PATH}"
    with open(CONFIGMAP_YAML_PATH, "r", encoding="utf-8") as f:
        docs = list(yaml.safe_load_all(f))
    assert len(docs) == 1
    cm = docs[0]
    raw_embedded = cm.get("data", {}).get("kubepredict-overview.json", "")
    assert len(raw_embedded.strip()) > 0

    embedded_json = json.loads(raw_embedded)
    canonical_json = _load_canonical_dashboard()

    assert embedded_json == canonical_json, (
        "ConfigMap embedded dashboard JSON does not match canonical dashboard JSON!"
    )
