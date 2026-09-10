import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

import app.routes
from app.factory import create_app
from config.settings import Settings


@pytest.fixture
def client():
    settings = Settings(max_cpu_iterations=500000, max_delay_ms=5000)
    registry = CollectorRegistry()
    app = create_app(settings=settings, metrics_registry=registry)
    return TestClient(app)


def test_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "adaptive-predictive-k8s-autoscaler"


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_readyz(client):
    app.routes.is_ready = True
    response = client.get("/readyz")
    assert response.status_code == 200

    app.routes.is_ready = False
    response = client.get("/readyz")
    assert response.status_code == 503
    app.routes.is_ready = True  # restore


def test_post_work_defaults(client):
    response = client.post("/work", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["cpu_iterations_completed"] == 100000
    assert data["delay_ms_applied"] == 0
    assert data["payload_size"] == 0
    assert "x-request-id" in response.headers


def test_post_work_small(client):
    response = client.post("/work", json={"cpu_iterations": 10, "delay_ms": 1, "payload_size": 5})
    assert response.status_code == 200
    assert response.json()["cpu_iterations_completed"] == 10


def test_post_work_exceed_cpu(client):
    response = client.post("/work", json={"cpu_iterations": 1000000})
    assert response.status_code == 422


def test_post_work_exceed_delay(client):
    response = client.post("/work", json={"delay_ms": 10000})
    assert response.status_code == 422


def test_post_work_invalid_schema(client):
    response = client.post("/work", json={"cpu_iterations": -1})
    assert response.status_code == 422


def test_post_work_controlled_failure(client):
    response = client.post("/work", json={"fail": True})
    assert response.status_code == 503
    assert response.json()["error"] == "synthetic_failure"


def test_request_id_preserved(client):
    response = client.post("/work", json={}, headers={"X-Request-ID": "my-test-id"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "my-test-id"


def test_invalid_request_id_replaced(client):
    response = client.post("/work", json={}, headers={"X-Request-ID": "invalid/id!"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "invalid/id!"


def test_unknown_route(client):
    response = client.get("/unknown_path_123")
    assert response.status_code == 404
