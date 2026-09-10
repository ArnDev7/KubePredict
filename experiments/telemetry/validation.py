"""Dataset validation module for Phase 7B telemetry datasets."""

import csv
import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

REQUIRED_TELEMETRY_COLUMNS = [
    "timestamp_utc",
    "run_id",
    "scenario_name",
    "elapsed_seconds",
    "scenario_stage",
    "configured_target_users",
    "configured_spawn_rate_per_second",
    "request_rate_per_second",
    "completed_work_rate_per_second",
    "http_5xx_rate_per_second",
    "rejected_work_rate_per_second",
    "p95_http_duration_seconds",
    "average_http_duration_seconds",
    "requests_in_progress",
    "cpu_iterations_rate_per_second",
    "configured_delay_rate_seconds_per_second",
    "prometheus_targets_up",
    "deployment_desired_replicas",
    "deployment_available_replicas",
    "deployment_ready_replicas",
    "source_interval_seconds",
]

REQUIRED_POD_TELEMETRY_COLUMNS = [
    "timestamp_utc",
    "pod",
    "request_rate_per_second",
    "work_rate_per_second",
    "cpu_millicores",
    "memory_bytes",
    "ready",
    "restart_count",
]

REQUIRED_DATASET_FILES = [
    "scenario_config.yaml",
    "metadata.json",
    "prometheus_series.json",
    "telemetry.csv",
    "pod_telemetry.csv",
    "kubernetes_samples.json",
    "locust_stats.csv",
    "run.log",
]


class DatasetValidationReport(BaseModel):
    """Validation report summarizing the structural integrity of a scenario dataset."""

    valid: bool
    run_id: str
    scenario_name: str
    sample_count: int
    pod_sample_count: int
    duration_seconds: float
    time_range_start: str | None = None
    time_range_end: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_value_counts: dict[str, int] = Field(default_factory=dict)
    interval_stats: dict[str, Any] = Field(default_factory=dict)


def validate_dataset(dataset_dir: Path | str) -> DatasetValidationReport:
    """Perform strict structural and semantic validation on a captured dataset directory."""
    d_path = Path(dataset_dir)
    errors: list[str] = []
    warnings: list[str] = []

    if not d_path.exists() or not d_path.is_dir():
        return DatasetValidationReport(
            valid=False,
            run_id=d_path.name,
            scenario_name="unknown",
            sample_count=0,
            pod_sample_count=0,
            duration_seconds=0.0,
            errors=[f"Dataset directory does not exist: {d_path}"],
        )

    # 1. Check required files
    for fname in REQUIRED_DATASET_FILES:
        if not (d_path / fname).exists():
            errors.append(f"Missing required file: {fname}")

    # If metadata or config is missing, abort early
    meta_path = d_path / "metadata.json"
    cfg_path = d_path / "scenario_config.yaml"
    if not meta_path.exists() or not cfg_path.exists():
        return DatasetValidationReport(
            valid=False,
            run_id=d_path.name,
            scenario_name="unknown",
            sample_count=0,
            pod_sample_count=0,
            duration_seconds=0.0,
            errors=errors,
        )

    # 2. Parse metadata & verify run ID and hashes
    with meta_path.open("r", encoding="utf-8") as f:
        meta_data = json.load(f)

    meta_run_id = meta_data.get("run_id", "")
    if meta_run_id != d_path.name:
        errors.append(f"Run ID mismatch: metadata={meta_run_id}, directory={d_path.name}")

    scenario_name = meta_data.get("scenario_name", "")
    expected_cfg_hash = meta_data.get("configuration_hash", "")
    actual_cfg_hash = sha256(cfg_path.read_bytes()).hexdigest()
    if expected_cfg_hash and expected_cfg_hash != actual_cfg_hash:
        errors.append(
            f"Config hash mismatch: expected={expected_cfg_hash}, actual={actual_cfg_hash}"
        )

    # 3. Validate telemetry.csv
    csv_path = d_path / "telemetry.csv"
    sample_count = 0
    timestamps: list[datetime] = []
    missing_value_counts: dict[str, int] = {}
    time_start: str | None = None
    time_end: str | None = None

    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []

            # Check required columns
            for col in REQUIRED_TELEMETRY_COLUMNS:
                if col not in header:
                    errors.append(f"telemetry.csv missing required column: {col}")

            for row_idx, row in enumerate(reader, start=1):
                sample_count += 1
                ts_str = row.get("timestamp_utc", "")
                try:
                    dt = datetime.fromisoformat(ts_str)
                    if dt.tzinfo is None:
                        errors.append(f"Row {row_idx}: timestamp_utc is not timezone-aware")
                    timestamps.append(dt)
                except (ValueError, TypeError) as ex:
                    errors.append(f"Row {row_idx}: invalid timestamp_utc '{ts_str}': {ex}")

                # Verify scenario_name and run_id match row
                if row.get("scenario_name") != scenario_name:
                    errors.append(
                        f"Row {row_idx}: scenario_name '{row.get('scenario_name')}' != '{scenario_name}'"
                    )
                if row.get("run_id") != meta_run_id:
                    errors.append(f"Row {row_idx}: run_id '{row.get('run_id')}' != '{meta_run_id}'")

                # Elapsed seconds non-negative
                try:
                    elapsed = float(row.get("elapsed_seconds", 0))
                    if elapsed < 0:
                        errors.append(f"Row {row_idx}: elapsed_seconds is negative: {elapsed}")
                except ValueError:
                    errors.append(f"Row {row_idx}: elapsed_seconds is non-numeric")

                # Fixed replicas invariant
                desired_rep = row.get("deployment_desired_replicas")
                if desired_rep != "" and desired_rep is not None:
                    try:
                        d_val = int(desired_rep)
                        if d_val != 2:
                            errors.append(
                                f"Row {row_idx}: fixed replica invariant violated (desired={d_val}, expected=2)"
                            )
                    except ValueError:
                        errors.append(f"Row {row_idx}: invalid deployment_desired_replicas")

                # Target status range [0, 2]
                targets_up = row.get("prometheus_targets_up")
                if targets_up != "" and targets_up is not None:
                    try:
                        t_val = float(targets_up)
                        if not (0.0 <= t_val <= 2.0):
                            errors.append(
                                f"Row {row_idx}: prometheus_targets_up out of bounds: {t_val}"
                            )
                    except ValueError:
                        errors.append(f"Row {row_idx}: invalid prometheus_targets_up value")

                # Check non-negative rates
                rate_cols = [
                    "request_rate_per_second",
                    "completed_work_rate_per_second",
                    "http_5xx_rate_per_second",
                    "rejected_work_rate_per_second",
                    "p95_http_duration_seconds",
                    "average_http_duration_seconds",
                    "cpu_iterations_rate_per_second",
                    "configured_delay_rate_seconds_per_second",
                ]
                for col in rate_cols:
                    val_str = row.get(col, "")
                    if val_str == "" or val_str is None:
                        missing_value_counts[col] = missing_value_counts.get(col, 0) + 1
                    else:
                        if val_str in ("NaN", "Inf", "+Inf", "-Inf"):
                            errors.append(
                                f"Row {row_idx}: illegal float string '{val_str}' in {col}"
                            )
                        else:
                            try:
                                v_num = float(val_str)
                                if v_num < 0:
                                    errors.append(f"Row {row_idx}: negative rate in {col}: {v_num}")
                            except ValueError:
                                errors.append(f"Row {row_idx}: non-numeric value in {col}")

        # Check monotonic timestamps and no duplicates
        for i in range(1, len(timestamps)):
            if timestamps[i] <= timestamps[i - 1]:
                errors.append(
                    f"Timestamp non-monotonic or duplicate at index {i}: "
                    f"{timestamps[i - 1].isoformat()} -> {timestamps[i].isoformat()}"
                )

        if timestamps:
            time_start = timestamps[0].isoformat()
            time_end = timestamps[-1].isoformat()

    # 4. Check interval stats and jitter
    interval_stats: dict[str, Any] = {}
    if len(timestamps) >= 2:
        intervals = [
            (timestamps[i] - timestamps[i - 1]).total_seconds() for i in range(1, len(timestamps))
        ]
        avg_int = sum(intervals) / len(intervals)
        min_int = min(intervals)
        max_int = max(intervals)
        interval_stats = {
            "average_interval_seconds": avg_int,
            "min_interval_seconds": min_int,
            "max_interval_seconds": max_int,
            "intervals_count": len(intervals),
        }
        if min_int < 10.0 or max_int > 25.0:
            warnings.append(
                f"Sampling interval jitter observed: min={min_int:.1f}s, max={max_int:.1f}s (expected ~15s)"
            )

    # 5. Check pod_telemetry.csv
    pod_csv_path = d_path / "pod_telemetry.csv"
    pod_sample_count = 0
    if pod_csv_path.exists():
        with pod_csv_path.open("r", encoding="utf-8") as f:
            p_reader = csv.DictReader(f)
            p_header = p_reader.fieldnames or []
            for col in REQUIRED_POD_TELEMETRY_COLUMNS:
                if col not in p_header:
                    errors.append(f"pod_telemetry.csv missing required column: {col}")

            for p_row_idx, p_row in enumerate(p_reader, start=1):
                pod_sample_count += 1
                p_pod = p_row.get("pod", "")
                if not p_pod:
                    errors.append(f"Row {p_row_idx} in pod_telemetry: pod column is blank")

                # Restart count non-negative
                rc_str = p_row.get("restart_count", "0")
                try:
                    if int(rc_str) < 0:
                        errors.append(f"Row {p_row_idx}: negative restart_count {rc_str}")
                except ValueError:
                    errors.append(f"Row {p_row_idx}: non-integer restart_count {rc_str}")

    # Plausibility check on sample count
    total_dur = meta_data.get("total_duration_seconds", 0)
    expected_samples = total_dur // 15
    if sample_count < max(1, expected_samples - 2):
        warnings.append(
            f"Sample count ({sample_count}) is lower than expected ({expected_samples}) for duration {total_dur}s"
        )

    is_valid = len(errors) == 0
    return DatasetValidationReport(
        valid=is_valid,
        run_id=meta_run_id,
        scenario_name=scenario_name,
        sample_count=sample_count,
        pod_sample_count=pod_sample_count,
        duration_seconds=float(total_dur),
        time_range_start=time_start,
        time_range_end=time_end,
        errors=errors,
        warnings=warnings,
        missing_value_counts=missing_value_counts,
        interval_stats=interval_stats,
    )
