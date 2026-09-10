"""CLI entrypoint for executing scenario telemetry capture."""

import argparse
import sys
from pathlib import Path

from experiments.telemetry.orchestrator import ScenarioCaptureOrchestrator
from load_testing.scenarios import get_scenario_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute a KubePredict request scenario and capture aligned Prometheus/Kubernetes telemetry."
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help="Path to scenario YAML file or registered name (e.g. steady, ramp, periodic, sudden_increase, drift).",
    )
    parser.add_argument(
        "--query-spec",
        default="experiments/configs/telemetry_queries.yaml",
        help="Path to telemetry query specification YAML.",
    )
    parser.add_argument(
        "--raw-output-root",
        default="data/raw/scenarios",
        help="Root directory for saving raw telemetry datasets.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Custom run ID (default: auto-generated run-<scenario>-<timestamp>).",
    )
    parser.add_argument(
        "--workload-port",
        type=int,
        default=18000,
        help="Local port for workload service port-forward (default: 18000).",
    )
    parser.add_argument(
        "--prometheus-port",
        type=int,
        default=19090,
        help="Local port for Prometheus service port-forward (default: 19090).",
    )
    parser.add_argument(
        "--context",
        default="kind-kubepredict-dev",
        help="Expected Kubernetes context.",
    )

    args = parser.parse_args()

    # Resolve scenario path if a name was passed
    scenario_arg = args.scenario
    if not scenario_arg.endswith(".yaml") and not scenario_arg.endswith(".yml"):
        scenario_path = get_scenario_path(scenario_arg)
    else:
        scenario_path = Path(scenario_arg)

    try:
        orchestrator = ScenarioCaptureOrchestrator(
            scenario_path=scenario_path,
            query_spec_path=args.query_spec,
            raw_output_root=args.raw_output_root,
            workload_port=args.workload_port,
            prometheus_port=args.prometheus_port,
            context=args.context,
        )
        dest_dir = orchestrator.execute(custom_run_id=args.run_id)
        print(f"\n[SUCCESS] Scenario capture completed. Dataset written to:\n  {dest_dir}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"\n[ERROR] Scenario capture failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
