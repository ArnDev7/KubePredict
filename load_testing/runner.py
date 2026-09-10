import argparse
import csv
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from load_testing.config import ScenarioConfig, load_scenario_config
from load_testing.metadata import RunMetadata, RunStatus, sanitize_run_id
from load_testing.scenarios import SCENARIO_NAMES, get_scenario_path


def parse_locust_summary(csv_prefix_path: Path) -> dict[str, Any]:
    """Extract aggregate request stats from Locust's output CSV."""
    stats_file = csv_prefix_path.parent / f"{csv_prefix_path.name}_stats.csv"
    if not stats_file.is_file():
        return {}

    summary: dict[str, Any] = {}
    try:
        with open(stats_file, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("Name", "").strip()
                if name.lower() == "aggregated":
                    summary = {
                        "request_count": int(row.get("Request Count", 0)),
                        "failure_count": int(row.get("Failure Count", 0)),
                        "median_response_time": float(row.get("Median Response Time", 0)),
                        "average_response_time": float(row.get("Average Response Time", 0)),
                        "min_response_time": float(row.get("Min Response Time", 0)),
                        "max_response_time": float(row.get("Max Response Time", 0)),
                        "requests_per_second": float(row.get("Requests/s", 0)),
                        "failures_per_second": float(row.get("Failures/s", 0)),
                    }
                    break
    except Exception as exc:  # noqa: BLE001
        summary["parse_error"] = str(exc)
    return summary


def run_dry_run(config: ScenarioConfig, config_path: Path) -> int:
    """Print a concise, deterministic summary of the scenario stages without generating traffic."""
    print("=== SCENARIO DRY-RUN VALIDATION ===")
    print(f"Scenario Name: {config.scenario_name}")
    print(f"Description:   {config.description}")
    print(f"Config File:   {config_path}")
    print(f"Target Host:   {config.host}")
    print(f"Endpoint:      {config.endpoint}")
    print(f"Total Duration:{config.total_duration_seconds}s")
    print(f"CPU Iters:     {config.cpu_iterations}")
    print(f"Delay (ms):    {config.delay_ms}")
    print(f"Payload Size:  {config.payload_size}")
    print(f"Random Seed:   {config.random_seed}")
    print(f"Timeout:       {config.request_timeout_seconds}s")
    print("\nDeterministic Stage Schedule:")
    print(f"  {'Stage':<6} | {'Duration':<10} | {'Target Users':<14} | {'Spawn Rate (u/s)':<16}")
    print("  " + "-" * 54)

    cumulative = 0
    for idx, stage in enumerate(config.stages, 1):
        window = f"{cumulative}s - {cumulative + stage.duration_seconds}s"
        print(
            f"  {idx:<6} | {window:<10} | {stage.target_users:<14} | {stage.spawn_rate_per_second:<16}"
        )
        cumulative += stage.duration_seconds

    print("=" * 56)
    print("Dry-run validation successful. No HTTP requests generated.")
    return 0


def execute_scenario(
    config: ScenarioConfig,
    config_path: Path,
    output_root: Path,
    run_id: str | None = None,
    application_image: str | None = None,
    cluster_name: str | None = None,
    controller_mode: str | None = None,
) -> int:
    """Run a scenario headlessly using Locust and record all metadata and artifacts."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    chosen_run_id = run_id or f"run_{config.scenario_name}_{timestamp}"
    clean_run_id = sanitize_run_id(chosen_run_id)

    run_dir = output_root / clean_run_id
    if run_dir.exists():
        raise FileExistsError(f"Output run directory already exists: {run_dir}")

    run_dir.mkdir(parents=True, exist_ok=False)

    config_content = config_path.read_text(encoding="utf-8")
    metadata = RunMetadata.create_initial(
        run_id=clean_run_id,
        config=config,
        config_path_str=str(config_path),
        config_content=config_content,
        application_image=application_image,
        cluster_name=cluster_name,
        controller_mode=controller_mode,
    )
    metadata_file = run_dir / "metadata.json"
    metadata.save(metadata_file)

    locustfile_path = Path(__file__).parent / "locustfile.py"
    csv_prefix = run_dir / "locust"
    logfile = run_dir / "run.log"

    env = os.environ.copy()
    env.pop("LOCUST_SKIP_MONKEY_PATCH", None)
    env["KUBEPREDICT_SCENARIO_CONFIG"] = str(config_path.resolve())

    # Assemble headless Locust command list (no shell=True)
    cmd = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        str(locustfile_path.resolve()),
        "--headless",
        "--host",
        config.host,
        "--run-time",
        f"{config.total_duration_seconds}s",
        "--csv",
        str(csv_prefix.resolve()),
        "--logfile",
        str(logfile.resolve()),
        "--only-summary",
    ]

    print(f"Starting scenario execution: '{config.scenario_name}' (Run ID: {clean_run_id})")
    print(f"Output directory: {run_dir}")
    print(f"Target host: {config.host}{config.endpoint}")
    print(f"Duration: {config.total_duration_seconds}s")

    exit_code = 1
    status: RunStatus = "failed"
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            check=False,
            timeout=config.total_duration_seconds + 30.0,
        )
        exit_code = proc.returncode
        status = "completed" if exit_code == 0 else "failed"
    except KeyboardInterrupt:
        print("\nScenario execution interrupted by user.")
        status = "interrupted"
        exit_code = 130
    except subprocess.TimeoutExpired:
        print(f"\nScenario timed out after {config.total_duration_seconds + 30} seconds.")
        status = "failed"
        exit_code = 124
    except Exception as exc:  # noqa: BLE001
        print(f"\nExecution failed with error: {exc}")
        status = "failed"
        exit_code = 1
    finally:
        # Collect generated artifact paths
        output_paths = {
            "metadata": str(metadata_file),
            "run_log": str(logfile),
        }
        for csv_type in ["stats", "stats_history", "failures", "exceptions"]:
            cf = run_dir / f"locust_{csv_type}.csv"
            if cf.is_file():
                output_paths[f"locust_{csv_type}"] = str(cf)

        actual_requests = parse_locust_summary(csv_prefix)
        metadata.finalize(
            status=status,
            output_paths=output_paths,
            actual_requests=actual_requests,
        )
        metadata.save(metadata_file)
        print(
            f"Finalized metadata: status={status}, requests recorded={actual_requests.get('request_count', 0)}"
        )

    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="KubePredict Reproducible Request Scenario Runner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help="Name of built-in scenario (steady, ramp, periodic, sudden_increase, drift) or path to YAML config file.",
    )
    parser.add_argument(
        "--output-root",
        default="experiments/output",
        help="Root directory for storing run artifacts and metadata.",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Override target host (e.g. http://127.0.0.1:8000).",
    )
    parser.add_argument(
        "--allow-non-local-host",
        action="store_true",
        help="Permit non-local destination host (for explicit user-owned development targets).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Explicit unique run ID for output folder naming.",
    )
    parser.add_argument(
        "--application-image",
        default=None,
        help="Workload container image tag for run provenance metadata.",
    )
    parser.add_argument(
        "--cluster-name",
        default=None,
        help="Kubernetes cluster name for run provenance metadata.",
    )
    parser.add_argument(
        "--controller-mode",
        default=None,
        help="Autoscaling controller mode under test (fixed, hpa, predictive, guarded).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and display stage summary without executing load.",
    )

    args = parser.parse_args(argv)

    # Resolve scenario config path
    scenario_arg = args.scenario
    if scenario_arg in SCENARIO_NAMES:
        config_path = get_scenario_path(scenario_arg)
    else:
        config_path = Path(scenario_arg)

    if not config_path.is_file():
        print(f"Error: Scenario configuration file not found at '{config_path}'.", file=sys.stderr)
        return 2

    try:
        config = load_scenario_config(
            config_path,
            allow_non_local_host=args.allow_non_local_host,
            host_override=args.host,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Configuration Validation Error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        return run_dry_run(config, config_path)

    output_root = Path(args.output_root)
    return execute_scenario(
        config=config,
        config_path=config_path,
        output_root=output_root,
        run_id=args.run_id,
        application_image=args.application_image,
        cluster_name=args.cluster_name,
        controller_mode=args.controller_mode,
    )


if __name__ == "__main__":
    sys.exit(main())
