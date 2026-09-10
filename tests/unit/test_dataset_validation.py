"""Unit tests for telemetry dataset structural and semantic validation."""

import csv
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from experiments.telemetry.validation import (
    REQUIRED_POD_TELEMETRY_COLUMNS,
    REQUIRED_TELEMETRY_COLUMNS,
    validate_dataset,
)


def _create_mock_valid_dataset(d_path: Path, run_id: str = "test-run-1") -> Path:
    d_path.mkdir(parents=True, exist_ok=True)

    # 1. Config
    cfg_bytes = b"scenario_name: steady\ntotal_duration_seconds: 60\n"
    (d_path / "scenario_config.yaml").write_bytes(cfg_bytes)
    cfg_hash = sha256(cfg_bytes).hexdigest()

    # 2. Metadata
    meta = {
        "run_id": run_id,
        "scenario_name": "steady",
        "configuration_hash": cfg_hash,
        "total_duration_seconds": 60,
        "status": "completed",
    }
    (d_path / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

    # 3. Other required files
    (d_path / "prometheus_series.json").write_text("{}", encoding="utf-8")
    (d_path / "kubernetes_samples.json").write_text("[]", encoding="utf-8")
    (d_path / "locust_stats.csv").write_text("Type,Name\n", encoding="utf-8")
    (d_path / "run.log").write_text("Started\nCompleted\n", encoding="utf-8")

    # 4. telemetry.csv
    base_ts = 1700000000.0
    with (d_path / "telemetry.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REQUIRED_TELEMETRY_COLUMNS)
        writer.writeheader()
        for i in range(4):
            dt = datetime.fromtimestamp(base_ts + (i * 15), tz=UTC)
            row = {
                "timestamp_utc": dt.isoformat(),
                "run_id": run_id,
                "scenario_name": "steady",
                "elapsed_seconds": f"{i * 15:.1f}",
                "scenario_stage": "stage_1",
                "configured_target_users": 10,
                "configured_spawn_rate_per_second": "2.0",
                "request_rate_per_second": "5.0",
                "completed_work_rate_per_second": "5.0",
                "http_5xx_rate_per_second": "0.0",
                "rejected_work_rate_per_second": "0.0",
                "p95_http_duration_seconds": "0.05",
                "average_http_duration_seconds": "0.02",
                "requests_in_progress": "1",
                "cpu_iterations_rate_per_second": "50000",
                "configured_delay_rate_seconds_per_second": "0.1",
                "prometheus_targets_up": "2.0",
                "deployment_desired_replicas": "2",
                "deployment_available_replicas": "2",
                "deployment_ready_replicas": "2",
                "source_interval_seconds": "15.0",
            }
            writer.writerow(row)

    # 5. pod_telemetry.csv
    with (d_path / "pod_telemetry.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REQUIRED_POD_TELEMETRY_COLUMNS)
        writer.writeheader()
        for i in range(4):
            dt = datetime.fromtimestamp(base_ts + (i * 15), tz=UTC)
            row = {
                "timestamp_utc": dt.isoformat(),
                "pod": "pod-1",
                "request_rate_per_second": "2.5",
                "work_rate_per_second": "2.5",
                "cpu_millicores": "10.0",
                "memory_bytes": "50000000",
                "ready": "true",
                "restart_count": "0",
            }
            writer.writerow(row)

    return d_path


def test_validate_valid_dataset(tmp_path: Path):
    d_path = tmp_path / "valid_run"
    _create_mock_valid_dataset(d_path, run_id="valid_run")

    report = validate_dataset(d_path)
    assert report.valid is True
    assert len(report.errors) == 0
    assert report.sample_count == 4
    assert report.pod_sample_count == 4
    assert report.scenario_name == "steady"


def test_validate_missing_file(tmp_path: Path):
    d_path = tmp_path / "missing_file_run"
    _create_mock_valid_dataset(d_path, run_id="missing_file_run")
    (d_path / "locust_stats.csv").unlink()

    report = validate_dataset(d_path)
    assert report.valid is False
    assert any("Missing required file: locust_stats.csv" in e for e in report.errors)


def test_validate_replica_invariant_violation(tmp_path: Path):
    d_path = tmp_path / "replica_violation_run"
    _create_mock_valid_dataset(d_path, run_id="replica_violation_run")

    # Overwrite telemetry.csv with desired=3
    rows = []
    with (d_path / "telemetry.csv").open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            r["deployment_desired_replicas"] = "3"
            rows.append(r)

    with (d_path / "telemetry.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REQUIRED_TELEMETRY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    report = validate_dataset(d_path)
    assert report.valid is False
    assert any("fixed replica invariant violated" in e for e in report.errors)


def test_validate_non_monotonic_timestamps(tmp_path: Path):
    d_path = tmp_path / "non_monotonic_run"
    _create_mock_valid_dataset(d_path, run_id="non_monotonic_run")

    rows = []
    with (d_path / "telemetry.csv").open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Make second timestamp earlier than first
    rows[1]["timestamp_utc"] = rows[0]["timestamp_utc"]

    with (d_path / "telemetry.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REQUIRED_TELEMETRY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    report = validate_dataset(d_path)
    assert report.valid is False
    assert any("non-monotonic or duplicate" in e for e in report.errors)
