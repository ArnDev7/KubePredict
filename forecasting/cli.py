"""Command-Line Interface and execution orchestrator for Phase 8A offline forecasting."""

import argparse
import csv
import hashlib
import json
import logging
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from forecasting.base import BaseForecaster, ForecastingConfig
from forecasting.ewma import EWMAForecaster
from forecasting.linear_trend import LinearTrendForecaster
from forecasting.moving_average import MovingAverageForecaster
from forecasting.persistence import PersistenceForecaster
from forecasting.preprocessing import (
    ScenarioData,
    discover_scenario_datasets,
    load_scenario_telemetry,
    partition_scenario,
)
from forecasting.rolling_evaluation import (
    ExcludedOriginRecord,
    PredictionRecord,
    compute_metrics_table,
    evaluate_scenario_split,
)
from forecasting.selection import (
    rank_validation_configurations,
    select_best_parameters_per_family,
    select_provisional_candidates,
)


def compute_file_sha256(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_git_metadata() -> dict[str, Any]:
    """Inspect local Git state (commit hash and dirty working-tree status)."""
    git_info: dict[str, Any] = {"commit": None, "is_dirty": False}
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            git_info["commit"] = res.stdout.strip()
        diff_res = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if diff_res.returncode == 0:
            git_info["is_dirty"] = bool(diff_res.stdout.strip())
    except (subprocess.SubprocessError, OSError):
        # Git executable is not installed or repo is not initialized
        pass
    return git_info


def load_config(config_path: Path | str) -> tuple[ForecastingConfig, str]:
    """Load and validate ForecastingConfig from YAML file, returning config and sha256."""
    p = Path(config_path)
    if not p.exists():
        raise FileNotFoundError(f"Configuration file not found: {p}")
    raw_bytes = p.read_bytes()
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
    raw_dict = yaml.safe_load(raw_bytes.decode("utf-8"))
    config = ForecastingConfig(**raw_dict)
    return config, raw_hash


def build_forecaster_catalog(config: ForecastingConfig) -> list[BaseForecaster]:
    """Instantiate all forecaster configurations defined by the configuration."""
    catalog: list[BaseForecaster] = [PersistenceForecaster()]

    for w in config.moving_average_windows:
        catalog.append(MovingAverageForecaster(window=w))

    for alpha in config.ewma_alphas:
        catalog.append(EWMAForecaster(alpha=alpha))

    for w in config.linear_trend_windows:
        catalog.append(LinearTrendForecaster(window=w))

    return catalog


def instantiate_frozen_forecaster(family: str, parameters: dict[str, Any]) -> BaseForecaster:
    """Instantiate a forecaster from frozen family and parameter dictionary."""
    if family == "persistence":
        return PersistenceForecaster()
    elif family == "moving_average":
        return MovingAverageForecaster(window=int(parameters["window"]))
    elif family == "ewma":
        return EWMAForecaster(alpha=float(parameters["alpha"]))
    elif family == "linear_trend":
        return LinearTrendForecaster(window=int(parameters["window"]))
    else:
        raise ValueError(f"Unknown model family: {family}")


def write_predictions_csv(predictions: list[PredictionRecord], out_path: Path) -> None:
    """Write forecast prediction records to CSV."""
    if not predictions:
        # Create empty file with headers
        fieldnames = list(PredictionRecord.__dataclass_fields__.keys())
        with open(out_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
        return

    fieldnames = list(predictions[0].to_dict().keys())
    with open(out_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in predictions:
            writer.writerow(p.to_dict())


def write_exclusions_csv(exclusions: list[ExcludedOriginRecord], out_path: Path) -> None:
    """Write excluded origins to CSV."""
    if not exclusions:
        fieldnames = list(ExcludedOriginRecord.__dataclass_fields__.keys())
        with open(out_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
        return

    fieldnames = list(exclusions[0].to_dict().keys())
    with open(out_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for e in exclusions:
            writer.writerow(e.to_dict())


def write_metrics_csv(metrics: list[Any], out_path: Path) -> None:
    """Write metric rows to CSV."""
    if not metrics:
        return
    fieldnames = list(metrics[0].model_dump().keys())
    with open(out_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for m in metrics:
            writer.writerow(m.model_dump())


def validate_mode(config_path: Path, dataset_root: Path) -> int:
    """Validate configuration and Phase 7B datasets without executing predictions."""
    print("=" * 70)
    print("PHASE 8A OFFLINE FORECASTING - VALIDATE MODE")
    print("=" * 70)

    try:
        config, cfg_hash = load_config(config_path)
        print(f"Configuration: {config_path} (SHA-256: {cfg_hash[:16]}...)")
        print(f"  Target: {config.target_column}")
        print(f"  Horizons: {config.forecast_horizon_steps} steps")
        print(
            f"  Split policy: train={1.0 - config.validation_fraction - config.test_fraction:.2f}, "
            f"val={config.validation_fraction:.2f}, test={config.test_fraction:.2f}"
        )
    except (ValueError, OSError, yaml.YAMLError) as ex:
        print(f"ERROR: Configuration validation failed: {ex}", file=sys.stderr)
        return 1

    discovered = discover_scenario_datasets(dataset_root)
    print(f"\nDiscovered {len(discovered)} scenario runs in {dataset_root}:")
    if len(discovered) < config.minimum_required_scenarios:
        print(
            f"ERROR: Required at least {config.minimum_required_scenarios} scenarios, "
            f"found only {len(discovered)}",
            file=sys.stderr,
        )
        return 1

    total_samples = 0
    valid_scenarios = 0

    for s_name, s_dir in discovered:
        csv_file = s_dir / "telemetry.csv"
        try:
            data = load_scenario_telemetry(csv_file, config.target_column, config.timestamp_column)
            splits = partition_scenario(
                data,
                config.validation_fraction,
                config.test_fraction,
                config.minimum_training_samples,
            )
            print(f"  Scenario '{s_name}' (run: {data.run_id}):")
            print(
                f"    Samples: {data.raw_sample_count} | Missing target values: {data.missing_count}"
            )
            print(f"    Avg interval: {data.average_interval_seconds:.2f}s")
            print(
                f"    Train: {splits.train.sample_count} samples ({splits.train.start_timestamp.isoformat()} -> {splits.train.end_timestamp.isoformat()})"
            )
            print(
                f"    Val:   {splits.validation.sample_count} samples ({splits.validation.start_timestamp.isoformat()} -> {splits.validation.end_timestamp.isoformat()})"
            )
            print(
                f"    Test:  {splits.test.sample_count} samples ({splits.test.start_timestamp.isoformat()} -> {splits.test.end_timestamp.isoformat()})"
            )
            total_samples += data.raw_sample_count
            valid_scenarios += 1
        except (ValueError, OSError) as ex:
            print(f"  ERROR inspecting {s_name}: {ex}", file=sys.stderr)

    catalog = build_forecaster_catalog(config)
    print(f"\nCatalog contains {len(catalog)} model configurations:")
    for m in catalog:
        print(f"  - {m.name}: {m.parameters}")

    print("\nPre-evaluation validation: PASS (No predictions executed, 0 mutations).")
    return 0 if valid_scenarios >= config.minimum_required_scenarios else 1


def evaluate_mode(config_path: Path, dataset_root: Path, output_root: Path | None = None) -> int:
    """Run full chronological offline evaluation across all Phase 7B datasets."""
    start_time = datetime.now(UTC)
    eval_id = f"eval-{start_time.strftime('%Y%m%d_%H%M%S')}"

    print("=" * 70)
    print(f"PHASE 8A OFFLINE FORECASTING - EVALUATE MODE ({eval_id})")
    print("=" * 70)

    config, cfg_hash = load_config(config_path)
    out_dir = Path(output_root or config.output_root) / eval_id
    summary_dir = Path(config.summary_output_root) / eval_id

    if out_dir.exists():
        print(f"ERROR: Output directory already exists: {out_dir}", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=False)
    summary_dir.mkdir(parents=True, exist_ok=True)

    # Setup execution logging
    log_file = out_dir / "execution.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    stream_handler = logging.StreamHandler(sys.stdout)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[file_handler, stream_handler],
        force=True,
    )
    logger = logging.getLogger("forecasting")
    logger.info("Starting Phase 8A offline forecasting evaluation: %s", eval_id)

    # 1. Discover and load datasets
    discovered = discover_scenario_datasets(dataset_root)
    if len(discovered) < config.minimum_required_scenarios:
        logger.error(
            "Found %d scenarios, below required minimum %d",
            len(discovered),
            config.minimum_required_scenarios,
        )
        return 1

    scenario_datasets: list[ScenarioData] = []
    scenario_splits: dict[str, Any] = {}
    input_file_hashes: dict[str, str] = {}
    data_quality_records: list[dict[str, Any]] = []

    for s_name, s_dir in discovered:
        csv_path = s_dir / "telemetry.csv"
        input_file_hashes[f"{s_name}/telemetry.csv"] = compute_file_sha256(csv_path)
        data = load_scenario_telemetry(csv_path, config.target_column, config.timestamp_column)
        splits = partition_scenario(
            data,
            config.validation_fraction,
            config.test_fraction,
            config.minimum_training_samples,
        )
        scenario_datasets.append(data)
        scenario_splits[s_name] = splits
        data_quality_records.append(
            {
                "scenario_name": s_name,
                "run_id": data.run_id,
                "raw_sample_count": data.raw_sample_count,
                "missing_target_values": data.missing_count,
                "average_interval_seconds": data.average_interval_seconds,
                "source_file": str(data.source_file),
                "train_samples": splits.train.sample_count,
                "validation_samples": splits.validation.sample_count,
                "test_samples": splits.test.sample_count,
            }
        )

    # 2. Instantiate forecaster catalog
    catalog = build_forecaster_catalog(config)
    logger.info("Evaluating %d candidate models on validation split...", len(catalog))

    val_predictions: list[PredictionRecord] = []
    val_exclusions: list[ExcludedOriginRecord] = []

    for forecaster in catalog:
        for data in scenario_datasets:
            splits = scenario_splits[data.scenario_name]
            for horizon in config.forecast_horizon_steps:
                preds, excls = evaluate_scenario_split(
                    forecaster=forecaster,
                    data=data,
                    splits=splits,
                    horizon=horizon,
                    split_name="validation",
                    config=config,
                )
                val_predictions.extend(preds)
                val_exclusions.extend(excls)

    val_metrics = compute_metrics_table(val_predictions, val_exclusions)
    val_rankings = rank_validation_configurations(
        val_metrics, primary_metric=config.primary_selection_metric
    )

    # 3. Select best parameters per family based exclusively on validation data
    best_params_per_family = select_best_parameters_per_family(
        val_metrics, primary_metric=config.primary_selection_metric
    )
    logger.info("Selected frozen parameters per model family and horizon:")
    for (m_fam, horizon), params in best_params_per_family.items():
        logger.info("  Horizon %d | %s -> %s", horizon, m_fam, params)

    # 4. Provisional candidate selection
    provisional_selection = select_provisional_candidates(
        validation_rows=val_metrics,
        generated_at=datetime.now(UTC).isoformat(),
        primary_metric=config.primary_selection_metric,
    )
    logger.info(
        "Overall Provisional Candidate: %s (status: %s)",
        provisional_selection.overall_candidate.model_name,
        provisional_selection.overall_candidate.status,
    )

    # 5. Evaluate frozen models ONCE on the held-out test split
    logger.info("Evaluating frozen models once on held-out test split...")
    test_predictions: list[PredictionRecord] = []
    test_exclusions: list[ExcludedOriginRecord] = []

    # Build frozen model instances for test evaluation
    for (m_fam, horizon), params in best_params_per_family.items():
        frozen_model = instantiate_frozen_forecaster(m_fam, params)
        for data in scenario_datasets:
            splits = scenario_splits[data.scenario_name]
            preds, excls = evaluate_scenario_split(
                forecaster=frozen_model,
                data=data,
                splits=splits,
                horizon=horizon,
                split_name="test",
                config=config,
            )
            test_predictions.extend(preds)
            test_exclusions.extend(excls)

    test_metrics = compute_metrics_table(test_predictions, test_exclusions)

    # 6. Save all required evidence to output directory
    end_time = datetime.now(UTC)

    # Copy config
    with open(out_dir / "evaluation_config.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(config.model_dump(), f, sort_keys=False)

    # Save dataset index
    with open(out_dir / "input_dataset_index.json", "w", encoding="utf-8") as f:
        json.dump(data_quality_records, f, indent=2)

    # Save data quality report
    with open(out_dir / "data_quality_report.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at": end_time.isoformat(),
                "scenarios_analyzed": len(scenario_datasets),
                "total_observations": sum(d.raw_sample_count for d in scenario_datasets),
                "total_missing_target_values": sum(d.missing_count for d in scenario_datasets),
                "scenario_details": data_quality_records,
            },
            f,
            indent=2,
        )

    # Save prediction CSVs
    write_predictions_csv(val_predictions, out_dir / "validation_predictions.csv")
    write_predictions_csv(test_predictions, out_dir / "test_predictions.csv")

    # Save metrics CSVs
    write_metrics_csv(val_metrics, out_dir / "validation_metrics.csv")
    write_metrics_csv(test_metrics, out_dir / "test_metrics.csv")
    write_metrics_csv(val_rankings, out_dir / "validation_ranking.csv")

    # Save provisional selection JSON
    with open(out_dir / "provisional_selection.json", "w", encoding="utf-8") as f:
        f.write(provisional_selection.model_dump_json(indent=2))

    # Save excluded origins CSV
    all_exclusions = val_exclusions + test_exclusions
    write_exclusions_csv(all_exclusions, out_dir / "excluded_origins.csv")

    # Run metadata JSON
    git_meta = get_git_metadata()
    run_meta = {
        "evaluation_id": eval_id,
        "started_at": start_time.isoformat(),
        "completed_at": end_time.isoformat(),
        "duration_seconds": (end_time - start_time).total_seconds(),
        "status": "COMPLETED",
        "python_version": platform.python_version(),
        "dependencies": {
            "numpy": np.__version__,
            "yaml": yaml.__version__,
        },
        "git_commit": git_meta["commit"],
        "git_dirty": git_meta["is_dirty"],
        "configuration_sha256": cfg_hash,
        "input_files": input_file_hashes,
        "model_families": sorted({m.name for m in catalog}),
        "horizons_steps": config.forecast_horizon_steps,
        "split_policy": {
            "train": 1.0 - config.validation_fraction - config.test_fraction,
            "validation": config.validation_fraction,
            "test": config.test_fraction,
        },
        "rolling_origin_rule": "target_contained_in_partition",
        "missing_value_policy": config.missing_value_policy,
        "total_validation_predictions": len(val_predictions),
        "total_test_predictions": len(test_predictions),
        "total_excluded_origins": len(all_exclusions),
        "output_directory": str(out_dir),
    }
    with open(out_dir / "run_metadata.json", "w", encoding="utf-8") as f:
        json.dump(run_meta, f, indent=2)

    # 7. Create concise summaries under results/summaries/forecasting/<eval_id>/
    summary_json = {
        "evaluation_id": eval_id,
        "started_at": start_time.isoformat(),
        "completed_at": end_time.isoformat(),
        "primary_selection_metric": config.primary_selection_metric,
        "overall_candidate": provisional_selection.overall_candidate.model_dump(),
        "per_horizon_candidates": [c.model_dump() for c in provisional_selection.per_horizon],
        "top_validation_rankings": [r.model_dump() for r in val_rankings[:10]],
    }
    with open(summary_dir / "evaluation_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary_json, f, indent=2)

    # Write concise Markdown summary
    with open(summary_dir / "evaluation_summary.md", "w", encoding="utf-8") as f:
        f.write(f"# Phase 8A Offline Forecasting Evaluation Summary: {eval_id}\n\n")
        f.write(f"- **Evaluated:** {start_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
        f.write(f"- **Primary Metric:** {config.primary_selection_metric.upper()}\n")
        f.write(
            f"- **Overall Provisional Candidate:** `{provisional_selection.overall_candidate.model_name}` "
            f"(Status: `{provisional_selection.overall_candidate.status}`)\n\n"
        )
        f.write("## Per-Horizon Provisional Candidates\n\n")
        f.write(
            "| Horizon (steps) | Candidate Model | Parameters | Val Macro MAE | Persistence MAE | Diff vs Persistence | Status |\n"
        )
        f.write("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n")
        f.writelines(
            f"| {c.horizon_steps} ({c.horizon_steps * 15}s) | `{c.model_name}` | `{json.dumps(c.parameters)}` | "
            f"{c.validation_macro_mae:.4f} | {c.persistence_macro_mae:.4f} | {c.mae_diff_vs_persistence:+.4f} | `{c.status}` |\n"
            for c in provisional_selection.per_horizon
        )
        f.write("\n## Research Constraint Note\n\n")
        f.write("> [!NOTE]\n")
        f.write(
            "> Candidate selection is strictly offline and provisional for Phase 8B review. "
            "No live autoscaling performance, latency improvements, or replica adjustments are implied.\n"
        )

    logger.info(
        "Evaluation complete! Outputs saved to:\n  Data: %s\n  Summary: %s", out_dir, summary_dir
    )
    file_handler.flush()
    file_handler.close()
    return 0


def inspect_mode(evaluation_dir: Path) -> int:
    """Inspect and display summary tables from an existing evaluation output directory."""
    eval_dir = Path(evaluation_dir)
    if not eval_dir.exists():
        print(f"ERROR: Directory does not exist: {eval_dir}", file=sys.stderr)
        return 1

    meta_file = eval_dir / "run_metadata.json"
    sel_file = eval_dir / "provisional_selection.json"
    val_rank_file = eval_dir / "validation_ranking.csv"
    test_metrics_file = eval_dir / "test_metrics.csv"

    for req_file in [meta_file, sel_file, val_rank_file, test_metrics_file]:
        if not req_file.exists():
            print(f"ERROR: Missing expected evaluation file: {req_file.name}", file=sys.stderr)
            return 1

    with open(meta_file, encoding="utf-8") as f:
        meta = json.load(f)

    with open(sel_file, encoding="utf-8") as f:
        sel = json.load(f)

    print("=" * 70)
    print(f"EVALUATION INSPECTION: {meta.get('evaluation_id')}")
    print("=" * 70)
    print(f"Started: {meta.get('started_at')} | Duration: {meta.get('duration_seconds', 0.0):.2f}s")
    print(f"Total Validation Predictions: {meta.get('total_validation_predictions')}")
    print(f"Total Test Predictions: {meta.get('total_test_predictions')}")
    print(f"Total Excluded Origins: {meta.get('total_excluded_origins')}")

    print("\n--- Per-Horizon Provisional Candidates ---")
    for c in sel.get("per_horizon", []):
        h = c.get("horizon_steps")
        print(f"  Horizon {h} ({h * 15}s): Model: {c.get('model_name')} {c.get('parameters')}")
        print(
            f"    Val Macro MAE: {c.get('validation_macro_mae'):.4f} | Persistence: {c.get('persistence_macro_mae'):.4f}"
        )
        print(f"    Status: {c.get('status')} | Reason: {c.get('selection_reason')}")
        if c.get("scenario_weaknesses"):
            print(f"    Weaknesses: {c.get('scenario_weaknesses')}")

    overall = sel.get("overall_candidate", {})
    print(
        f"\n--- Overall Candidate: {overall.get('model_name')} {overall.get('parameters')} "
        f"({overall.get('status')}) ---"
    )

    print("\nTop 5 Validation Rankings:")
    with open(val_rank_file, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i >= 5:
                break
            print(
                f"  Rank {row.get('rank')}: {row.get('model_name')} {row.get('parameters_json')} "
                f"[Horizon {row.get('horizon_steps')}] Macro MAE: {float(row.get('macro_mae', 0)):.4f} "
                f"Weighted MAE: {float(row.get('weighted_mae', 0)):.4f}"
            )

    return 0


def main() -> int:
    """CLI main entry point."""
    parser = argparse.ArgumentParser(
        prog="python -m forecasting.cli",
        description="KubePredict Phase 8A Offline Forecasting CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate subcommand
    val_p = subparsers.add_parser("validate", help="Validate config and Phase 7B datasets")
    val_p.add_argument(
        "--config",
        type=Path,
        default=Path("experiments/configs/forecasting.yaml"),
        help="Path to forecasting configuration YAML",
    )
    val_p.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("data/raw/scenarios"),
        help="Path to Phase 7B datasets root",
    )

    # evaluate subcommand
    eval_p = subparsers.add_parser("evaluate", help="Run offline forecasting evaluation")
    eval_p.add_argument(
        "--config",
        type=Path,
        default=Path("experiments/configs/forecasting.yaml"),
        help="Path to forecasting configuration YAML",
    )
    eval_p.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("data/raw/scenarios"),
        help="Path to Phase 7B datasets root",
    )
    eval_p.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Optional override for output directory root",
    )

    # inspect subcommand
    insp_p = subparsers.add_parser(
        "inspect", help="Inspect an existing evaluation output directory"
    )
    insp_p.add_argument(
        "--evaluation-dir",
        type=Path,
        required=True,
        help="Path to evaluation directory in data/processed/forecasting/<eval_id>",
    )

    args = parser.parse_args()

    if args.command == "validate":
        return validate_mode(args.config, args.dataset_root)
    elif args.command == "evaluate":
        return evaluate_mode(args.config, args.dataset_root, args.output_root)
    elif args.command == "inspect":
        return inspect_mode(args.evaluation_dir)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
