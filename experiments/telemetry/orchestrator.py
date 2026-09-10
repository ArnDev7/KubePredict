"""Scenario Telemetry Orchestrator for Phase 7B."""

import csv
import json
import logging
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from experiments.telemetry.collector import KubernetesCollector, KubernetesSnapshot
from experiments.telemetry.prometheus_client import PrometheusClient, RangeQueryResult
from experiments.telemetry.query_spec import (
    TelemetryQueryConfig,
    compute_query_spec_hash,
    load_query_spec,
)
from experiments.telemetry.validation import validate_dataset
from load_testing.config import ScenarioConfig, load_scenario_config
from load_testing.scenarios.base import ScenarioProfile

logger = logging.getLogger("experiments.telemetry.orchestrator")


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a local TCP port is currently open/in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def wait_for_http_ready(url: str, timeout_seconds: float = 15.0) -> bool:
    """Poll an HTTP health/ready endpoint until 200 OK or timeout."""
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(0.5)
    return False


class ScenarioCaptureOrchestrator:
    """Manages the full lifecycle of executing a request scenario and capturing aligned telemetry."""

    def __init__(
        self,
        scenario_path: Path | str,
        query_spec_path: Path | str = "experiments/configs/telemetry_queries.yaml",
        raw_output_root: Path | str = "data/raw/scenarios",
        workload_port: int = 18000,
        prometheus_port: int = 19090,
        context: str = "kind-kubepredict-dev",
        namespace: str = "kubepredict-system",
        monitoring_namespace: str = "kubepredict-monitoring",
    ) -> None:
        self.scenario_path = Path(scenario_path)
        self.query_spec_path = Path(query_spec_path)
        self.raw_output_root = Path(raw_output_root)
        self.workload_port = workload_port
        self.prometheus_port = prometheus_port
        self.context = context
        self.namespace = namespace
        self.monitoring_namespace = monitoring_namespace

        self.scenario_config: ScenarioConfig = load_scenario_config(self.scenario_path)
        self.query_config: TelemetryQueryConfig = load_query_spec(self.query_spec_path)
        self.profile = ScenarioProfile(self.scenario_config)

        self.collector = KubernetesCollector(
            context=self.context,
            namespace=self.namespace,
            deployment_name="kubepredict-workload-api",
        )
        self.prom_client = PrometheusClient(
            base_url=f"http://127.0.0.1:{self.prometheus_port}",
            timeout_seconds=15.0,
            allow_non_local=False,
        )

        self._workload_pf_proc: subprocess.Popen | None = None
        self._prometheus_pf_proc: subprocess.Popen | None = None

    def execute(self, custom_run_id: str | None = None) -> Path:
        """Run the full scenario capture lifecycle and return the created dataset directory."""
        now_utc = datetime.now(UTC)
        run_id = (
            custom_run_id
            or f"run-{self.scenario_config.scenario_name}-{now_utc.strftime('%Y%m%d_%H%M%S')}"
        )

        dest_dir = self.raw_output_root / self.scenario_config.scenario_name / run_id
        if dest_dir.exists():
            raise FileExistsError(f"Dataset directory already exists: {dest_dir}")

        # Temporary locust runner output root
        locust_temp_dir = dest_dir / "_locust_temp"
        dest_dir.mkdir(parents=True, exist_ok=False)

        log_file = dest_dir / "run.log"
        run_logs: list[str] = []

        def log(msg: str) -> None:
            ts = datetime.now(UTC).isoformat()
            line = f"[{ts}] {msg}"
            run_logs.append(line)
            logger.info(msg)

        log(
            f"Starting Phase 7B scenario capture: {self.scenario_config.scenario_name} (run_id={run_id})"
        )

        status = "started"
        started_at: datetime | None = None
        completed_at: datetime | None = None
        raw_prom_results: dict[str, Any] = {}
        k8s_snapshots: list[KubernetesSnapshot] = []

        try:
            # 1. Pre-flight environment assertions
            log("Verifying cluster context, fixed replicas, and HPA absence...")
            self._verify_preflight(log)

            # 2. Start port-forwards
            log(f"Starting port-forward for workload on port {self.workload_port}...")
            self._start_workload_port_forward()

            log(f"Starting port-forward for Prometheus on port {self.prometheus_port}...")
            self._start_prometheus_port_forward()

            # Wait for endpoints
            if not wait_for_http_ready(f"http://127.0.0.1:{self.workload_port}/healthz", 15.0):
                raise RuntimeError(f"Workload port-forward failed on port {self.workload_port}")
            if not wait_for_http_ready(f"http://127.0.0.1:{self.prometheus_port}/-/ready", 15.0):
                raise RuntimeError(f"Prometheus port-forward failed on port {self.prometheus_port}")
            log("Port-forwards established and verified ready.")

            # Record initial pre-run snapshot
            snap_init = self.collector.collect_snapshot()
            k8s_snapshots.append(snap_init)

            # 3. Execute Locust scenario headlessly
            started_at = datetime.now(UTC)
            log(
                f"Executing Locust scenario: total_duration={self.scenario_config.total_duration_seconds}s..."
            )

            runner_cmd = [
                sys.executable,
                "-m",
                "load_testing.runner",
                "--scenario",
                str(self.scenario_path.resolve()),
                "--output-root",
                str(locust_temp_dir.resolve()),
                "--host",
                f"http://127.0.0.1:{self.workload_port}",
                "--run-id",
                run_id,
            ]

            runner_res = subprocess.run(runner_cmd, capture_output=True, text=True, check=False)
            if runner_res.returncode != 0:
                log(f"Locust runner exited with non-zero code {runner_res.returncode}")
                log(f"Runner stdout:\n{runner_res.stdout[:1000]}")
                log(f"Runner stderr:\n{runner_res.stderr[:1000]}")
                raise RuntimeError(f"Locust execution failed (code {runner_res.returncode})")

            completed_at = datetime.now(UTC)
            log(
                f"Locust scenario completed successfully in {(completed_at - started_at).total_seconds():.1f}s."
            )

            # 4. Allow post-run Prometheus scrape interval buffer (20 seconds)
            log("Allowing 20-second scrape buffer for final Prometheus metrics collection...")
            time.sleep(20.0)

            # Record post-run snapshot
            snap_post = self.collector.collect_snapshot()
            k8s_snapshots.append(snap_post)

            # 5. Query Prometheus range data
            query_start = started_at.timestamp() - 15.0
            query_end = completed_at.timestamp() + 15.0
            log(
                f"Querying Prometheus range data from {query_start:.1f} to {query_end:.1f} (step={self.query_config.step})..."
            )

            system_range_results: list[RangeQueryResult] = []
            for q_def in self.query_config.queries:
                try:
                    res = self.prom_client.query_range(
                        field_name=q_def.field_name,
                        promql=q_def.promql,
                        start_time=query_start,
                        end_time=query_end,
                        step=self.query_config.step,
                    )
                    system_range_results.append(res)
                    raw_prom_results[q_def.field_name] = res.model_dump(mode="json")
                except Exception as ex:
                    if q_def.required:
                        raise
                    log(f"Optional query {q_def.field_name} returned no data or error: {ex}")

            pod_range_results: list[RangeQueryResult] = []
            for q_def in self.query_config.pod_queries:
                try:
                    res = self.prom_client.query_range(
                        field_name=q_def.field_name,
                        promql=q_def.promql,
                        start_time=query_start,
                        end_time=query_end,
                        step=self.query_config.step,
                    )
                    pod_range_results.append(res)
                    raw_prom_results[q_def.field_name] = res.model_dump(mode="json")
                except (RuntimeError, ValueError) as ex:
                    log(f"Pod query {q_def.field_name} failed: {ex}")

            # 6. Normalize and align telemetry
            log("Normalizing system and pod time series to CSV...")
            self._write_aligned_csvs(
                dest_dir=dest_dir,
                run_id=run_id,
                started_at=started_at,
                system_results=system_range_results,
                pod_results=pod_range_results,
                snapshots=k8s_snapshots,
            )

            # 7. Copy and preserve artifacts
            shutil.copyfile(self.scenario_path, dest_dir / "scenario_config.yaml")

            # Copy locust output CSVs from locust_temp_dir/<run_id>
            locust_run_dir = locust_temp_dir / run_id
            for csv_name in ["locust_stats.csv", "locust_stats_history.csv", "locust_failures.csv"]:
                src_f = locust_run_dir / csv_name
                if src_f.exists():
                    shutil.copyfile(src_f, dest_dir / csv_name)

            # Save raw Prometheus JSON
            with (dest_dir / "prometheus_series.json").open("w", encoding="utf-8") as f:
                json.dump(raw_prom_results, f, indent=2)

            # Save Kubernetes snapshots JSON
            with (dest_dir / "kubernetes_samples.json").open("w", encoding="utf-8") as f:
                json.dump([s.model_dump(mode="json") for s in k8s_snapshots], f, indent=2)

            # Clean up temp locust dir
            if locust_temp_dir.exists():
                shutil.rmtree(locust_temp_dir, ignore_errors=True)

            status = "completed"
            log("Raw dataset generated. Running structural validation...")

        except Exception as exc:
            status = "failed"
            log(f"Scenario capture failed with error: {exc}")
            raise
        finally:
            # Tear down port-forwards
            self._stop_port_forwards(log)

            # Write metadata.json
            cfg_bytes = self.scenario_path.read_bytes()
            cfg_hash = sha256(cfg_bytes).hexdigest()
            query_hash = compute_query_spec_hash(self.query_spec_path)

            meta_record = {
                "run_id": run_id,
                "scenario_name": self.scenario_config.scenario_name,
                "started_at": started_at.isoformat() if started_at else None,
                "completed_at": completed_at.isoformat() if completed_at else None,
                "status": status,
                "host": f"http://127.0.0.1:{self.workload_port}",
                "endpoint": self.scenario_config.endpoint,
                "random_seed": self.scenario_config.random_seed,
                "configuration_file": str(self.scenario_path),
                "configuration_hash": cfg_hash,
                "query_specification_file": str(self.query_spec_path),
                "query_specification_hash": query_hash,
                "total_duration_seconds": self.scenario_config.total_duration_seconds,
                "source_interval_seconds": 15.0,
                "stages": [s.model_dump() for s in self.scenario_config.stages],
                "cpu_iterations": self.scenario_config.cpu_iterations,
                "delay_ms": self.scenario_config.delay_ms,
                "payload_size": self.scenario_config.payload_size,
                "application_image": "kubepredict-workload-api:0.1.0",
                "cluster_name": "kubepredict-dev",
                "controller_mode": "fixed",
                "output_paths": {
                    "metadata": str(dest_dir / "metadata.json"),
                    "telemetry_csv": str(dest_dir / "telemetry.csv"),
                    "pod_telemetry_csv": str(dest_dir / "pod_telemetry.csv"),
                    "prometheus_series": str(dest_dir / "prometheus_series.json"),
                    "kubernetes_samples": str(dest_dir / "kubernetes_samples.json"),
                    "validation_report": str(dest_dir / "validation_report.json"),
                    "run_log": str(dest_dir / "run.log"),
                },
            }

            with (dest_dir / "metadata.json").open("w", encoding="utf-8") as f:
                json.dump(meta_record, f, indent=2)

            # Write run.log
            with log_file.open("w", encoding="utf-8") as f:
                f.write("\n".join(run_logs) + "\n")

            # Validate dataset
            report = validate_dataset(dest_dir)
            with (dest_dir / "validation_report.json").open("w", encoding="utf-8") as f:
                json.dump(report.model_dump(mode="json"), f, indent=2)

            log(
                f"Validation report: valid={report.valid} samples={report.sample_count} errors={len(report.errors)}"
            )

        return dest_dir

    def _verify_preflight(self, log_fn) -> None:
        """Verify context, fixed replicas, and HPA absence before run."""
        # 1. Check current context
        ctx_cmd = ["kubectl", "config", "current-context"]
        res = subprocess.run(ctx_cmd, capture_output=True, text=True, check=True)
        cur_ctx = res.stdout.strip()
        if cur_ctx != self.context:
            raise RuntimeError(f"Context mismatch: expected {self.context}, got {cur_ctx}")

        # 2. Check fixed replicas
        snap = self.collector.collect_snapshot()
        if snap.desired_replicas != 2:
            raise RuntimeError(
                f"Fixed replica invariant violated: desired={snap.desired_replicas} (expected 2)"
            )
        if snap.ready_replicas != 2:
            raise RuntimeError(f"Workload not ready: ready={snap.ready_replicas} (expected 2)")

        # 3. Check HPA absence
        hpa_cmd = [
            "kubectl",
            "get",
            "hpa",
            "-A",
            "--context",
            self.context,
            "--ignore-not-found=true",
        ]
        hpa_res = subprocess.run(hpa_cmd, capture_output=True, text=True, check=True)
        if hpa_res.stdout.strip() != "":
            raise RuntimeError(f"Live HPA detected in cluster! Execution blocked: {hpa_res.stdout}")

        # 4. Check ports free
        if is_port_in_use(self.workload_port):
            raise RuntimeError(f"Local workload port {self.workload_port} is already in use")
        if is_port_in_use(self.prometheus_port):
            raise RuntimeError(f"Local Prometheus port {self.prometheus_port} is already in use")

    def _start_workload_port_forward(self) -> None:
        cmd = [
            "kubectl",
            "port-forward",
            "service/kubepredict-workload-api",
            f"{self.workload_port}:8000",
            "-n",
            self.namespace,
            "--context",
            self.context,
        ]
        self._workload_pf_proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

    def _start_prometheus_port_forward(self) -> None:
        cmd = [
            "kubectl",
            "port-forward",
            "service/kubepredict-prometheus",
            f"{self.prometheus_port}:9090",
            "-n",
            self.monitoring_namespace,
            "--context",
            self.context,
        ]
        self._prometheus_pf_proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

    def _stop_port_forwards(self, log_fn) -> None:
        """Safely terminate port-forwards and ensure ports are released."""
        for name, proc, port in [
            ("workload", self._workload_pf_proc, self.workload_port),
            ("prometheus", self._prometheus_pf_proc, self.prometheus_port),
        ]:
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3.0)
                except (subprocess.SubprocessError, OSError):
                    try:
                        proc.kill()
                    except (subprocess.SubprocessError, OSError) as kill_err:
                        log_fn(f"Failed to kill {name} PID {proc.pid}: {kill_err}")
                log_fn(f"Stopped {name} port-forward (PID {proc.pid}).")

        # Verify ports release
        time.sleep(1.0)
        for port in [self.workload_port, self.prometheus_port]:
            if is_port_in_use(port):
                log_fn(f"WARNING: Port {port} still appears in use after termination")

    def _write_aligned_csvs(
        self,
        dest_dir: Path,
        run_id: str,
        started_at: datetime,
        system_results: list[RangeQueryResult],
        pod_results: list[RangeQueryResult],
        snapshots: list[KubernetesSnapshot],
    ) -> None:
        """Align Prometheus time series, stage schedules, and Kubernetes metrics to CSVs."""
        # 1. Collect all distinct timestamps across all system queries
        ts_map: dict[datetime, dict[str, float | None]] = {}
        for res in system_results:
            for s in res.series:
                for sample in s.samples:
                    ts = sample.timestamp
                    if ts not in ts_map:
                        ts_map[ts] = {}
                    ts_map[ts][res.field_name] = sample.value

        sorted_timestamps = sorted(ts_map.keys())

        # Build telemetry.csv
        system_csv_path = dest_dir / "telemetry.csv"
        from experiments.telemetry.validation import REQUIRED_TELEMETRY_COLUMNS

        # Determine baseline replicas from snapshots
        desired_rep = 2
        avail_rep = 2
        ready_rep = 2
        if snapshots:
            last_snap = snapshots[-1]
            desired_rep = last_snap.desired_replicas
            avail_rep = last_snap.available_replicas
            ready_rep = last_snap.ready_replicas

        with system_csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=REQUIRED_TELEMETRY_COLUMNS)
            writer.writeheader()

            for ts in sorted_timestamps:
                elapsed = max(0.0, (ts - started_at).total_seconds())
                stage_info = self.profile.get_stage_at_time(elapsed)
                if stage_info is not None:
                    target_users, spawn_rate = stage_info
                else:
                    target_users, spawn_rate = 0, 0.0
                stage_idx = self.profile.get_stage_index_at_time(elapsed)

                vals = ts_map[ts]
                row: dict[str, Any] = {
                    "timestamp_utc": ts.isoformat(),
                    "run_id": run_id,
                    "scenario_name": self.scenario_config.scenario_name,
                    "elapsed_seconds": f"{elapsed:.1f}",
                    "scenario_stage": f"stage_{stage_idx}",
                    "configured_target_users": target_users,
                    "configured_spawn_rate_per_second": f"{spawn_rate:.1f}",
                    "request_rate_per_second": _fmt(vals.get("request_rate_per_second")),
                    "completed_work_rate_per_second": _fmt(
                        vals.get("completed_work_rate_per_second")
                    ),
                    "http_5xx_rate_per_second": _fmt(vals.get("http_5xx_rate_per_second")),
                    "rejected_work_rate_per_second": _fmt(
                        vals.get("rejected_work_rate_per_second")
                    ),
                    "p95_http_duration_seconds": _fmt(vals.get("p95_http_duration_seconds")),
                    "average_http_duration_seconds": _fmt(
                        vals.get("average_http_duration_seconds")
                    ),
                    "requests_in_progress": _fmt(vals.get("requests_in_progress")),
                    "cpu_iterations_rate_per_second": _fmt(
                        vals.get("cpu_iterations_rate_per_second")
                    ),
                    "configured_delay_rate_seconds_per_second": _fmt(
                        vals.get("configured_delay_rate_seconds_per_second")
                    ),
                    "prometheus_targets_up": _fmt(vals.get("prometheus_targets_up")),
                    "deployment_desired_replicas": desired_rep,
                    "deployment_available_replicas": avail_rep,
                    "deployment_ready_replicas": ready_rep,
                    "source_interval_seconds": "15.0",
                }
                writer.writerow(row)

        # 2. Build pod_telemetry.csv
        pod_csv_path = dest_dir / "pod_telemetry.csv"
        from experiments.telemetry.validation import REQUIRED_POD_TELEMETRY_COLUMNS

        # Map per-pod metrics: (timestamp, pod) -> dict of field_name -> value
        pod_map: dict[tuple[datetime, str], dict[str, float | None]] = {}
        for res in pod_results:
            for s in res.series:
                p_name = s.labels.get("pod", "unknown")
                for sample in s.samples:
                    key = (sample.timestamp, p_name)
                    if key not in pod_map:
                        pod_map[key] = {}
                    pod_map[key][res.field_name] = sample.value

        # Match with latest snapshot pod metrics
        pod_top_metrics: dict[str, tuple[float | None, int | None]] = {}
        pod_ready_status: dict[str, tuple[bool, int]] = {}
        if snapshots:
            for p_obs in snapshots[-1].pods:
                pod_top_metrics[p_obs.pod_name] = (p_obs.cpu_millicores, p_obs.memory_bytes)
                pod_ready_status[p_obs.pod_name] = (p_obs.ready, p_obs.restart_count)

        with pod_csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=REQUIRED_POD_TELEMETRY_COLUMNS)
            writer.writeheader()

            for (ts, pod), p_vals in sorted(pod_map.items()):
                cpu_m, mem_b = pod_top_metrics.get(pod, (None, None))
                is_r, rc = pod_ready_status.get(pod, (True, 0))

                p_row: dict[str, Any] = {
                    "timestamp_utc": ts.isoformat(),
                    "pod": pod,
                    "request_rate_per_second": _fmt(p_vals.get("pod_http_request_rate_per_second")),
                    "work_rate_per_second": _fmt(p_vals.get("pod_work_request_rate_per_second")),
                    "cpu_millicores": _fmt(cpu_m),
                    "memory_bytes": _fmt(mem_b),
                    "ready": "true" if is_r else "false",
                    "restart_count": rc,
                }
                writer.writerow(p_row)


def _fmt(val: float | None) -> str:
    """Format numeric values cleanly, preserving missing data as empty string."""
    if val is None:
        return ""
    if isinstance(val, int):
        return str(val)
    return f"{val:.4f}"
