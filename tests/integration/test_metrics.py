import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from app.factory import create_app
from config.settings import Settings


@pytest.fixture
def client_and_registry():
    settings = Settings()
    registry = CollectorRegistry()
    app = create_app(settings=settings, metrics_registry=registry)
    return TestClient(app), registry


def test_metrics_endpoint(client_and_registry):
    client, _ = client_and_registry
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "apa_http_requests_total" in response.text


def test_metrics_increment_on_success(client_and_registry):
    client, registry = client_and_registry
    client.post("/work", json={"cpu_iterations": 10, "delay_ms": 1})

    val = registry.get_sample_value("apa_work_requests_total", {"outcome": "completed"})
    assert val == 1.0

    val_cpu = registry.get_sample_value("apa_cpu_iterations_total")
    assert val_cpu == 10.0

    val_delay = registry.get_sample_value("apa_artificial_delay_seconds_total")
    assert val_delay == 0.001

    val_prog = registry.get_sample_value(
        "apa_http_requests_in_progress", {"method": "POST", "route": "/work"}
    )
    assert val_prog == 0.0


def test_metrics_increment_on_failure(client_and_registry):
    client, registry = client_and_registry
    client.post("/work", json={"fail": True})

    val = registry.get_sample_value("apa_work_requests_total", {"outcome": "controlled_failure"})
    assert val == 1.0

    val_prog = registry.get_sample_value(
        "apa_http_requests_in_progress", {"method": "POST", "route": "/work"}
    )
    assert val_prog == 0.0


def test_metrics_increment_on_rejection(client_and_registry):
    client, registry = client_and_registry
    client.post("/work", json={"cpu_iterations": -1})  # invalid schema

    val = registry.get_sample_value("apa_work_requests_total", {"outcome": "rejected"})
    assert val == 1.0
