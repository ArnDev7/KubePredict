import json
from pathlib import Path

from load_testing.metadata import RunMetadata, compute_config_hash, sanitize_run_id
from load_testing.scenarios import load_named_scenario


def test_sanitize_run_id() -> None:
    assert sanitize_run_id("run/with/slashes") == "run_with_slashes"
    assert sanitize_run_id("run with spaces") == "run_with_spaces"
    assert sanitize_run_id("run-safe_123") == "run-safe_123"
    assert sanitize_run_id("!@#$%^&*") == "run_default"


def test_metadata_initialization_and_serialization(tmp_path: Path) -> None:
    cfg = load_named_scenario("steady")
    raw_content = "dummy: yaml"
    meta = RunMetadata.create_initial(
        run_id="test_run_01",
        config=cfg,
        config_path_str="load_testing/scenarios/steady.yaml",
        config_content=raw_content,
        application_image="kubepredict-workload-api:0.1.0",
        cluster_name="kubepredict-dev",
        controller_mode="fixed",
    )

    assert meta.run_id == "test_run_01"
    assert meta.scenario_name == "steady"
    assert meta.status == "started"
    assert meta.started_at.tzinfo is not None
    assert meta.completed_at is None
    assert meta.configuration_hash == compute_config_hash(raw_content)

    # Save to disk
    meta_path = tmp_path / "metadata.json"
    meta.save(meta_path)
    assert meta_path.is_file()

    # Finalize as completed
    meta.finalize(
        status="completed",
        output_paths={"run_log": str(tmp_path / "run.log")},
        actual_requests={"request_count": 150, "failure_count": 0},
    )
    assert meta.status == "completed"
    assert meta.completed_at is not None
    assert meta.completed_at >= meta.started_at
    assert meta.actual_requests.get("request_count") == 150

    meta.save(meta_path)
    with open(meta_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["status"] == "completed"
    assert data["actual_requests"]["request_count"] == 150

    # Ensure no secret fields exist
    meta_json_str = json.dumps(data)
    assert "password" not in meta_json_str.lower()
    assert "token" not in meta_json_str.lower()
    assert "secret" not in meta_json_str.lower()


def test_metadata_interrupted_status() -> None:
    cfg = load_named_scenario("steady")
    meta = RunMetadata.create_initial(
        run_id="interrupted_run",
        config=cfg,
        config_path_str="test.yaml",
        config_content="test",
    )
    meta.finalize(status="interrupted", notes="User aborted")
    assert meta.status == "interrupted"
    assert meta.completed_at is not None
    assert "User aborted" in (meta.notes or "")
