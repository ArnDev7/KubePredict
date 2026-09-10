"""Unit tests for telemetry query specification loading, hashing, and validation."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from experiments.telemetry.query_spec import (
    QueryDefinition,
    TelemetryQueryConfig,
    compute_query_spec_hash,
    load_query_spec,
)


def test_valid_query_definition():
    q = QueryDefinition(
        field_name="test_rate",
        description="Test rate description",
        source="prometheus",
        promql="rate(http_requests_total[1m])",
        expected_result_type="matrix",
        aggregation="rate",
        unit="req/s",
        required=True,
    )
    assert q.field_name == "test_rate"
    assert q.source == "prometheus"
    assert q.required is True


def test_query_definition_missing_fields():
    with pytest.raises(ValidationError):
        QueryDefinition(field_name="", description="Test", promql="")


def test_query_definition_invalid_source():
    with pytest.raises(ValidationError):
        QueryDefinition(
            field_name="test",
            description="desc",
            source="cloudwatch",  # type: ignore[arg-type]
            promql="test",
            expected_result_type="matrix",
            aggregation="sum",
            unit="req/s",
        )


def test_telemetry_query_config_duplicate_fields():
    q1 = QueryDefinition(
        field_name="dup_rate",
        description="desc 1",
        promql="metric_1",
        expected_result_type="matrix",
        aggregation="sum",
        unit="req/s",
    )
    q2 = QueryDefinition(
        field_name="dup_rate",
        description="desc 2",
        promql="metric_2",
        expected_result_type="matrix",
        aggregation="sum",
        unit="req/s",
    )
    with pytest.raises(ValidationError, match="Duplicate system query field name"):
        TelemetryQueryConfig(queries=[q1, q2])


def test_telemetry_query_config_duplicate_pod_fields():
    q1 = QueryDefinition(
        field_name="metric",
        description="desc 1",
        promql="metric_1",
        expected_result_type="matrix",
        aggregation="sum",
        unit="req/s",
    )
    q_pod1 = QueryDefinition(
        field_name="pod_dup",
        description="desc 1",
        promql="metric_pod",
        expected_result_type="matrix",
        aggregation="sum",
        unit="req/s",
    )
    q_pod2 = QueryDefinition(
        field_name="pod_dup",
        description="desc 2",
        promql="metric_pod",
        expected_result_type="matrix",
        aggregation="sum",
        unit="req/s",
    )
    with pytest.raises(ValidationError, match="Duplicate pod query field name"):
        TelemetryQueryConfig(queries=[q1], pod_queries=[q_pod1, q_pod2])


def test_load_committed_telemetry_queries(tmp_path: Path):
    path = Path("experiments/configs/telemetry_queries.yaml")
    assert path.exists()
    cfg = load_query_spec(path)
    assert len(cfg.queries) >= 10
    assert len(cfg.pod_queries) >= 2
    h1 = compute_query_spec_hash(path)
    h2 = compute_query_spec_hash(path)
    assert h1 == h2
    assert len(h1) == 64
