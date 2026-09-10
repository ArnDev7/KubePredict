"""Integration tests for the complete Phase 8A offline forecasting pipeline.

Uses isolated test-only synthetic scenario fixtures to test validate and evaluate flows
without contacting Kubernetes or external networks.
"""

import json
from pathlib import Path

import pytest
import yaml

from forecasting.cli import evaluate_mode, inspect_mode, validate_mode


@pytest.fixture
def synthetic_scenario_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Create isolated test-only synthetic scenario datasets and configuration."""
    data_root = tmp_path / "data" / "raw" / "scenarios"
    cfg_path = tmp_path / "forecasting_test_config.yaml"

    # Create 4 synthetic scenarios: steady, ramp, periodic, drift
    scenarios = ["steady", "ramp", "periodic", "drift"]
    dataset_records: list[dict[str, str]] = []

    for scen in scenarios:
        scen_dir = data_root / scen / f"run-{scen}-synthetic"
        scen_dir.mkdir(parents=True, exist_ok=True)
        csv_path = scen_dir / "telemetry.csv"

        # Generate 15 sample rows for each scenario
        lines = ["timestamp_utc,scenario_name,run_id,request_rate_per_second"]
        for i in range(15):
            ts = f"2026-09-10T12:{i:02d}:00+00:00"
            rate = 10.0 + float(i * 2) if scen != "periodic" else (10.0 if i % 2 == 0 else 20.0)
            lines.append(f"{ts},{scen},run-{scen}-synthetic,{rate}")

        csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        dataset_records.append(
            {
                "scenario": scen,
                "run_id": f"run-{scen}-synthetic",
                "relative_path": f"{scen}/run-{scen}-synthetic",
            }
        )

    dataset_index = {"generated_at": "2026-09-10T12:00:00Z", "datasets": dataset_records}
    # Write dataset-index.json
    (data_root / "dataset-index.json").write_text(
        json.dumps(dataset_index, indent=2), encoding="utf-8"
    )

    # Create test configuration
    config_dict = {
        "target_column": "request_rate_per_second",
        "timestamp_column": "timestamp_utc",
        "scenario_column": "scenario_name",
        "sampling_interval_seconds": 15.0,
        "forecast_horizon_steps": [1, 2],
        "minimum_training_samples": 6,
        "validation_fraction": 0.20,
        "test_fraction": 0.20,
        "rolling_step_size": 1,
        "moving_average_windows": [3],
        "ewma_alphas": [0.5],
        "linear_trend_windows": [3],
        "primary_selection_metric": "mae",
        "secondary_selection_metrics": ["rmse", "smape"],
        "minimum_required_scenarios": 4,
        "random_seed": 42,
        "missing_value_policy": "exclude_origin",
        "output_root": str((tmp_path / "data" / "processed" / "forecasting").as_posix()),
        "summary_output_root": str((tmp_path / "results" / "summaries" / "forecasting").as_posix()),
    }

    cfg_path.write_text(yaml.safe_dump(config_dict), encoding="utf-8")
    return cfg_path, data_root


def test_integration_pipeline_validate_mode(synthetic_scenario_fixture: tuple[Path, Path]) -> None:
    """Verify validate mode succeeds without creating any output files or predictions."""
    cfg_path, data_root = synthetic_scenario_fixture
    ret = validate_mode(config_path=cfg_path, dataset_root=data_root)
    assert ret == 0


def test_integration_pipeline_evaluate_mode(
    synthetic_scenario_fixture: tuple[Path, Path], tmp_path: Path
) -> None:
    """Verify complete evaluate flow produces all required files, metrics, and ranking."""
    cfg_path, data_root = synthetic_scenario_fixture
    output_root = tmp_path / "data" / "processed" / "forecasting"

    ret = evaluate_mode(config_path=cfg_path, dataset_root=data_root, output_root=output_root)
    assert ret == 0

    # Locate generated evaluation directory
    eval_dirs = list(output_root.iterdir())
    assert len(eval_dirs) == 1
    eval_dir = eval_dirs[0]

    # Verify all expected files are present
    expected_files = [
        "evaluation_config.yaml",
        "input_dataset_index.json",
        "data_quality_report.json",
        "validation_predictions.csv",
        "test_predictions.csv",
        "validation_metrics.csv",
        "test_metrics.csv",
        "validation_ranking.csv",
        "provisional_selection.json",
        "excluded_origins.csv",
        "run_metadata.json",
        "execution.log",
    ]
    for fname in expected_files:
        p = eval_dir / fname
        assert p.exists(), f"Missing required output file: {fname}"
        assert p.stat().st_size > 0, f"Output file is empty: {fname}"

    # Verify provisional selection content
    with open(eval_dir / "provisional_selection.json", encoding="utf-8") as f:
        sel = json.load(f)
    assert "per_horizon" in sel
    assert "overall_candidate" in sel
    assert len(sel["per_horizon"]) == 2  # horizons 1 and 2

    # Verify inspect mode works on the generated evaluation directory
    inspect_ret = inspect_mode(eval_dir)
    assert inspect_ret == 0
