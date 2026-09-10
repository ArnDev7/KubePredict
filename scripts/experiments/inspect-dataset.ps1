# Phase 7B Dataset Inspection Script
param (
    [Parameter(Mandatory = $true)]
    [string]$DatasetPath
)

$ErrorActionPreference = "Stop"

Write-Host "=== Inspecting KubePredict Telemetry Dataset ===" -ForegroundColor Cyan
Write-Host "Path: $DatasetPath`n"

$pyInspectScript = @'
import sys
import json
from pathlib import Path
from experiments.telemetry.validation import validate_dataset

if len(sys.argv) < 2:
    print("Error: No dataset path provided.")
    sys.exit(1)

d_path = Path(sys.argv[1])
if not d_path.exists():
    print(f"Error: Path does not exist: {d_path}")
    sys.exit(1)

report = validate_dataset(d_path)

meta_file = d_path / 'metadata.json'
meta = {}
if meta_file.exists():
    with meta_file.open('r', encoding='utf-8') as f:
        meta = json.load(f)

print(f"Scenario Name:      {report.scenario_name}")
print(f"Run ID:             {report.run_id}")
print(f"Status:             {meta.get('status', 'unknown')}")
print(f"Validation Status:  {'VALID' if report.valid else 'INVALID'}")
print(f"System Samples:     {report.sample_count}")
print(f"Pod Samples:        {report.pod_sample_count}")
print(f"Duration (sec):     {report.duration_seconds}")
print(f"Time Range Start:   {report.time_range_start}")
print(f"Time Range End:     {report.time_range_end}")

if report.interval_stats:
    print(f"Interval Stats:     avg={report.interval_stats.get('average_interval_seconds', 0):.2f}s, min={report.interval_stats.get('min_interval_seconds', 0):.2f}s, max={report.interval_stats.get('max_interval_seconds', 0):.2f}s")

if report.missing_value_counts:
    print("\nMissing Value Counts by Field:")
    for k, v in report.missing_value_counts.items():
        print(f"  - {k}: {v}")

if report.errors:
    print("\nValidation Errors:")
    for err in report.errors:
        print(f"  [ERROR] {err}")

if report.warnings:
    print("\nValidation Warnings:")
    for w in report.warnings:
        print(f"  [WARN] {w}")
'@

$pyInspectScript | .venv\Scripts\python.exe - "$DatasetPath"
