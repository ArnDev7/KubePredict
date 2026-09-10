"""Unit tests for preprocessing and chronological splitting."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from forecasting.preprocessing import (
    ScenarioData,
    load_scenario_telemetry,
    parse_iso_utc,
    partition_scenario,
)


def test_parse_iso_utc_valid() -> None:
    """Verify parsing valid ISO string with UTC offset."""
    dt = parse_iso_utc("2026-09-10T15:46:26.801000+00:00")
    assert dt.tzinfo is not None
    assert dt.tzinfo == UTC


def test_parse_iso_utc_naive_rejected() -> None:
    """Verify naive timestamp without timezone is rejected."""
    with pytest.raises(ValueError, match="must be timezone-aware"):
        parse_iso_utc("2026-09-10T15:46:26.801000")


def test_load_scenario_telemetry_csv(tmp_path: Path) -> None:
    """Verify loading from a generated valid CSV file."""
    csv_file = tmp_path / "telemetry.csv"
    csv_file.write_text(
        "timestamp_utc,scenario_name,run_id,request_rate_per_second\n"
        "2026-09-10T15:00:00+00:00,test_scen,run-1,10.5\n"
        "2026-09-10T15:00:15+00:00,test_scen,run-1,\n"
        "2026-09-10T15:00:30+00:00,test_scen,run-1,20.0\n",
        encoding="utf-8",
    )

    data = load_scenario_telemetry(csv_file)
    assert data.scenario_name == "test_scen"
    assert data.run_id == "run-1"
    assert data.raw_sample_count == 3
    assert data.missing_count == 1
    assert data.target_values == [10.5, None, 20.0]
    assert data.average_interval_seconds == pytest.approx(15.0)


def test_load_scenario_duplicate_timestamp_rejected(tmp_path: Path) -> None:
    """Verify error on duplicate timestamps."""
    csv_file = tmp_path / "telemetry.csv"
    csv_file.write_text(
        "timestamp_utc,scenario_name,run_id,request_rate_per_second\n"
        "2026-09-10T15:00:00+00:00,test_scen,run-1,10.5\n"
        "2026-09-10T15:00:00+00:00,test_scen,run-1,15.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate timestamps"):
        load_scenario_telemetry(csv_file)


def test_partition_scenario_chronological_splits() -> None:
    """Verify chronological partition splits with no overlap and no shuffle."""
    base_ts = datetime(2026, 9, 10, 15, 0, 0, tzinfo=UTC)
    ts_list = [base_ts + timedelta(seconds=i * 15) for i in range(15)]
    vals: list[float | None] = [float(i) for i in range(15)]
    data = ScenarioData(
        scenario_name="scen_15",
        run_id="run-1",
        timestamps=ts_list,
        target_values=vals,
        raw_sample_count=15,
        missing_count=0,
        average_interval_seconds=15.0,
        source_file=Path("dummy.csv"),
    )

    # 15 samples: 20% val = 3, 20% test = 3, train = 9
    splits = partition_scenario(
        data, validation_fraction=0.20, test_fraction=0.20, minimum_training_samples=6
    )
    assert splits.train.sample_count == 9
    assert splits.train.start_index == 0
    assert splits.train.end_index == 9

    assert splits.validation.sample_count == 3
    assert splits.validation.start_index == 9
    assert splits.validation.end_index == 12

    assert splits.test.sample_count == 3
    assert splits.test.start_index == 12
    assert splits.test.end_index == 15

    # Check timestamps continuity
    assert splits.train.end_timestamp < splits.validation.start_timestamp
    assert splits.validation.end_timestamp < splits.test.start_timestamp


def test_partition_scenario_insufficient_samples() -> None:
    """Verify error when samples yield insufficient training points."""
    base_ts = datetime(2026, 9, 10, 15, 0, 0, tzinfo=UTC)
    ts_list = [base_ts + timedelta(seconds=i * 15) for i in range(8)]
    vals: list[float | None] = [float(i) for i in range(8)]
    data = ScenarioData(
        scenario_name="short_scen",
        run_id="run-short",
        timestamps=ts_list,
        target_values=vals,
        raw_sample_count=8,
        missing_count=0,
        average_interval_seconds=15.0,
        source_file=Path("dummy.csv"),
    )

    # 8 samples: val=1, test=1, train=6. With minimum_training_samples=7 -> must raise
    with pytest.raises(ValueError, match="below minimum required"):
        partition_scenario(
            data, validation_fraction=0.20, test_fraction=0.20, minimum_training_samples=7
        )
