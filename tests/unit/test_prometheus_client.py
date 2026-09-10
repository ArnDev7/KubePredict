"""Unit tests for Prometheus range-query client and matrix response parsing."""

from datetime import UTC, datetime

import httpx
import pytest

from experiments.telemetry.prometheus_client import PrometheusClient


def test_prometheus_client_host_validation():
    with pytest.raises(ValueError, match="must be localhost"):
        PrometheusClient(base_url="http://prometheus.example.com:9090", allow_non_local=False)

    # Localhost allowed
    client1 = PrometheusClient(base_url="http://127.0.0.1:9090")
    assert client1.base_url == "http://127.0.0.1:9090"

    client2 = PrometheusClient(base_url="http://localhost:19090")
    assert client2.base_url == "http://localhost:19090"

    # Explicit override allowed
    client3 = PrometheusClient(base_url="http://prometheus.example.com:9090", allow_non_local=True)
    assert client3.base_url == "http://prometheus.example.com:9090"


def test_parse_sample_value():
    assert PrometheusClient._parse_sample_value("12.34") == 12.34
    assert PrometheusClient._parse_sample_value(42) == 42.0
    assert PrometheusClient._parse_sample_value("NaN") is None
    assert PrometheusClient._parse_sample_value("+Inf") is None
    assert PrometheusClient._parse_sample_value("-Inf") is None
    assert PrometheusClient._parse_sample_value("invalid") is None
    assert PrometheusClient._parse_sample_value(None) is None


def test_query_range_success(monkeypatch):
    client = PrometheusClient(base_url="http://127.0.0.1:9090")

    mock_resp_data = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"pod": "pod-1", "job": "kubepredict-workload"},
                    "values": [
                        [1700000000.0, "10.5"],
                        [1700000015.0, "15.2"],
                        [1700000030.0, "NaN"],
                    ],
                }
            ],
        },
    }

    class MockResponse:
        status_code = 200

        def json(self):
            return mock_resp_data

    def mock_get(self, url, params=None):
        return MockResponse()

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    start_dt = datetime.fromtimestamp(1700000000.0, tz=UTC)
    end_dt = datetime.fromtimestamp(1700000030.0, tz=UTC)

    result = client.query_range(
        field_name="test_field",
        promql="rate(http_requests_total[1m])",
        start_time=start_dt,
        end_time=end_dt,
        step="15s",
    )

    assert result.field_name == "test_field"
    assert result.result_type == "matrix"
    assert len(result.series) == 1
    s = result.series[0]
    assert s.labels.get("pod") == "pod-1"
    assert len(s.samples) == 3
    assert s.samples[0].value == 10.5
    assert s.samples[1].value == 15.2
    assert s.samples[2].value is None
    assert s.samples[0].timestamp.tzinfo == UTC


def test_query_range_api_error(monkeypatch):
    client = PrometheusClient(base_url="http://127.0.0.1:9090")

    class MockResponse:
        status_code = 200

        def json(self):
            return {
                "status": "error",
                "errorType": "bad_data",
                "error": "PromQL syntax error",
            }

    monkeypatch.setattr(httpx.Client, "get", lambda self, url, params=None: MockResponse())

    with pytest.raises(RuntimeError, match="Prometheus API error \\[bad_data\\]"):
        client.query_range("err_field", "bad query", 100, 200)


def test_query_range_empty_matrix(monkeypatch):
    client = PrometheusClient(base_url="http://127.0.0.1:9090")

    class MockResponse:
        status_code = 200

        def json(self):
            return {"status": "success", "data": {"resultType": "matrix", "result": []}}

    monkeypatch.setattr(httpx.Client, "get", lambda self, url, params=None: MockResponse())

    res = client.query_range("empty_field", "up", 100, 200)
    assert res.series == []
