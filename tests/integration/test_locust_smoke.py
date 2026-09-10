import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
import yaml

from load_testing.config import load_scenario_config
from load_testing.runner import execute_scenario, main


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def local_app_server():
    """Start a temporary loopback Uvicorn server running the workload app."""
    port = find_free_port()
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    proc = subprocess.Popen(cmd)
    health_url = f"http://127.0.0.1:{port}/healthz"

    # Wait up to 5s for server to start
    ready = False
    for _ in range(25):
        time.sleep(0.2)
        try:
            with urllib.request.urlopen(health_url, timeout=1) as resp:
                if resp.status == 200:
                    ready = True
                    break
        except Exception:  # noqa: BLE001, S110
            pass

    if not ready:
        proc.kill()
        pytest.fail(f"Local test server failed to start on port {port}")

    yield port

    # Cleanup
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_locust_runner_smoke_execution(tmp_path: Path, local_app_server: int) -> None:
    port = local_app_server
    # Create a 3-second smoke configuration
    smoke_config = {
        "scenario_name": "pytest_smoke",
        "description": "Short integration smoke test for Locust runner",
        "total_duration_seconds": 5,
        "host": f"http://127.0.0.1:{port}",
        "endpoint": "/work",
        "random_seed": 42,
        "cpu_iterations": 500,
        "delay_ms": 0,
        "payload_size": 10,
        "request_timeout_seconds": 2.0,
        "stages": [
            {"duration_seconds": 5, "target_users": 2, "spawn_rate_per_second": 2.0},
        ],
    }
    config_file = tmp_path / "smoke.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(smoke_config, f)

    cfg = load_scenario_config(config_file)
    output_root = tmp_path / "output"

    exit_code = execute_scenario(
        config=cfg,
        config_path=config_file,
        output_root=output_root,
        run_id="pytest_smoke_run",
    )

    assert exit_code == 0
    run_dir = output_root / "pytest_smoke_run"
    assert run_dir.is_dir()

    meta_file = run_dir / "metadata.json"
    assert meta_file.is_file()

    import json

    with open(meta_file, "r", encoding="utf-8") as f:
        meta = json.load(f)

    assert meta["status"] == "completed"
    assert meta["run_id"] == "pytest_smoke_run"
    assert "actual_requests" in meta
    # Ensure at least 1 request was successfully recorded
    req_count = meta["actual_requests"].get("request_count", 0)
    assert req_count > 0, f"Expected at least 1 request, got {req_count}"


def test_runner_rejects_duplicate_run_dir(tmp_path: Path, local_app_server: int) -> None:
    port = local_app_server
    smoke_config = {
        "scenario_name": "dup_test",
        "description": "Test preventing directory overwrite",
        "total_duration_seconds": 2,
        "host": f"http://127.0.0.1:{port}",
        "endpoint": "/work",
        "random_seed": 42,
        "cpu_iterations": 100,
        "delay_ms": 0,
        "payload_size": 0,
        "request_timeout_seconds": 2.0,
        "stages": [
            {"duration_seconds": 2, "target_users": 1, "spawn_rate_per_second": 1.0},
        ],
    }
    config_file = tmp_path / "dup.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(smoke_config, f)

    cfg = load_scenario_config(config_file)
    output_root = tmp_path / "output"
    run_dir = output_root / "dup_run"
    run_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(FileExistsError, match="already exists"):
        execute_scenario(
            config=cfg,
            config_path=config_file,
            output_root=output_root,
            run_id="dup_run",
        )


def test_cli_runner_invalid_config_exits_nonzero() -> None:
    # Non-existent scenario
    ret = main(["--scenario", "non_existent_scenario_12345"])
    assert ret != 0
