"""Data loading, quality inspection, and chronological partitioning for scenario telemetry."""

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field


@dataclass(frozen=True)
class ScenarioData:
    """In-memory chronological scenario telemetry series."""

    scenario_name: str
    run_id: str
    timestamps: list[datetime]
    target_values: list[float | None]
    raw_sample_count: int
    missing_count: int
    average_interval_seconds: float
    source_file: Path


class ChronologicalSplit(BaseModel):
    """Indices and timestamp bounds for a single chronological partition."""

    name: str  # 'train', 'validation', 'test'
    start_index: int = Field(ge=0)
    end_index: int = Field(ge=0)  # Exclusive end index
    sample_count: int = Field(ge=0)
    start_timestamp: datetime
    end_timestamp: datetime


class ScenarioSplits(BaseModel):
    """Chronological split partitioning for a scenario run."""

    scenario_name: str
    run_id: str
    train: ChronologicalSplit
    validation: ChronologicalSplit
    test: ChronologicalSplit


def parse_iso_utc(ts_str: str) -> datetime:
    """Parse ISO timestamp string, ensuring timezone-aware UTC datetime."""
    dt = datetime.fromisoformat(ts_str)
    if dt.tzinfo is None:
        raise ValueError(f"Timestamp must be timezone-aware: {ts_str}")
    return dt.astimezone(UTC)


def load_scenario_telemetry(
    telemetry_csv_path: Path | str,
    target_column: str = "request_rate_per_second",
    timestamp_column: str = "timestamp_utc",
) -> ScenarioData:
    """Load, validate, and chronologically sort scenario telemetry from CSV."""
    path = Path(telemetry_csv_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Telemetry file not found: {path}")

    timestamps: list[datetime] = []
    target_values: list[float | None] = []
    scenario_names: set[str] = set()
    run_ids: set[str] = set()

    with open(path, mode="r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"Telemetry CSV {path} has no header")
        if target_column not in reader.fieldnames:
            raise ValueError(f"Target column '{target_column}' missing from {path}")
        if timestamp_column not in reader.fieldnames:
            raise ValueError(f"Timestamp column '{timestamp_column}' missing from {path}")

        for row_idx, row in enumerate(reader, start=2):
            raw_ts = row.get(timestamp_column, "").strip()
            if not raw_ts:
                raise ValueError(f"Row {row_idx}: missing timestamp in {path}")
            ts = parse_iso_utc(raw_ts)

            raw_target = row.get(target_column, "").strip()
            val: float | None = None
            if raw_target:
                try:
                    fval = float(raw_target)
                    if np.isfinite(fval):
                        val = fval
                    else:
                        val = None
                except ValueError:
                    val = None

            timestamps.append(ts)
            target_values.append(val)

            s_name = row.get("scenario_name", "").strip()
            if s_name:
                scenario_names.add(s_name)
            r_id = row.get("run_id", "").strip()
            if r_id:
                run_ids.add(r_id)

    if not timestamps:
        raise ValueError(f"Telemetry CSV {path} contains 0 rows")

    # Detect duplicate timestamps
    if len(set(timestamps)) != len(timestamps):
        raise ValueError(f"Telemetry CSV {path} contains duplicate timestamps")

    # Verify chronological ordering or sort
    is_sorted = all(timestamps[i] <= timestamps[i + 1] for i in range(len(timestamps) - 1))
    if not is_sorted:
        combined = sorted(zip(timestamps, target_values, strict=True), key=lambda pair: pair[0])
        timestamps = [p[0] for p in combined]
        target_values = [p[1] for p in combined]

    # Calculate intervals
    if len(timestamps) > 1:
        intervals = [
            (timestamps[i + 1] - timestamps[i]).total_seconds() for i in range(len(timestamps) - 1)
        ]
        avg_interval = float(np.mean(intervals))
    else:
        avg_interval = 15.0

    scenario_name = next(iter(scenario_names)) if scenario_names else path.parent.name
    run_id = next(iter(run_ids)) if run_ids else path.parent.name
    missing_count = sum(1 for v in target_values if v is None)

    return ScenarioData(
        scenario_name=scenario_name,
        run_id=run_id,
        timestamps=timestamps,
        target_values=target_values,
        raw_sample_count=len(timestamps),
        missing_count=missing_count,
        average_interval_seconds=avg_interval,
        source_file=path,
    )


def partition_scenario(
    data: ScenarioData,
    validation_fraction: float = 0.20,
    test_fraction: float = 0.20,
    minimum_training_samples: int = 6,
) -> ScenarioSplits:
    """Partition scenario observations chronologically into train, validation, and test splits.

    Split logic (chronological, no shuffle, no overlap):
        N = total samples
        N_val = max(1, floor(N * validation_fraction))
        N_test = max(1, floor(N * test_fraction))
        N_train = N - N_val - N_test
    """
    n = data.raw_sample_count
    if (
        validation_fraction <= 0
        or test_fraction <= 0
        or (validation_fraction + test_fraction >= 1.0)
    ):
        raise ValueError("Invalid partition fractions")

    n_val = max(1, int(np.floor(n * validation_fraction)))
    n_test = max(1, int(np.floor(n * test_fraction)))
    n_train = n - n_val - n_test

    if n_train < minimum_training_samples:
        raise ValueError(
            f"Scenario {data.scenario_name} ({data.run_id}) has {n} samples, yielding "
            f"{n_train} training samples, which is below minimum required {minimum_training_samples}"
        )

    # Indices:
    # Train: [0, n_train)
    # Val:   [n_train, n_train + n_val)
    # Test:  [n_train + n_val, n)
    val_start = n_train
    val_end = n_train + n_val
    test_start = val_end
    test_end = n

    train_split = ChronologicalSplit(
        name="train",
        start_index=0,
        end_index=n_train,
        sample_count=n_train,
        start_timestamp=data.timestamps[0],
        end_timestamp=data.timestamps[n_train - 1],
    )
    val_split = ChronologicalSplit(
        name="validation",
        start_index=val_start,
        end_index=val_end,
        sample_count=n_val,
        start_timestamp=data.timestamps[val_start],
        end_timestamp=data.timestamps[val_end - 1],
    )
    test_split = ChronologicalSplit(
        name="test",
        start_index=test_start,
        end_index=test_end,
        sample_count=n_test,
        start_timestamp=data.timestamps[test_start],
        end_timestamp=data.timestamps[test_end - 1],
    )

    return ScenarioSplits(
        scenario_name=data.scenario_name,
        run_id=data.run_id,
        train=train_split,
        validation=val_split,
        test=test_split,
    )


def discover_scenario_datasets(scenarios_root: Path | str) -> list[tuple[str, Path]]:
    """Discover valid scenario run directories containing telemetry.csv."""
    root = Path(scenarios_root)
    discovered: list[tuple[str, Path]] = []

    # Check for dataset-index.json first
    index_path = root / "dataset-index.json"
    if index_path.exists():
        with open(index_path, mode="r", encoding="utf-8") as f:
            idx = json.load(f)
        for entry in idx.get("datasets", []):
            rel = entry.get("relative_path", "")
            s_name = entry.get("scenario", "")
            run_dir = root / rel
            if (run_dir / "telemetry.csv").exists():
                discovered.append((s_name, run_dir))
        if discovered:
            return sorted(discovered, key=lambda x: x[0])

    # Fallback to direct directory discovery
    for scenario_dir in sorted(root.iterdir()):
        if not scenario_dir.is_dir():
            continue
        # Find subdirectories or direct run directories
        for sub in sorted(scenario_dir.iterdir()):
            if sub.is_dir() and (sub / "telemetry.csv").exists():
                discovered.append((scenario_dir.name, sub))
        if (scenario_dir / "telemetry.csv").exists():
            discovered.append((scenario_dir.name, scenario_dir))

    return sorted(discovered, key=lambda x: x[0])
