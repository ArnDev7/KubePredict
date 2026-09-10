# Request Scenario Framework (Phase 7A)

## 1. Overview & Purpose

The **KubePredict Request Scenario Framework** is a deterministic, configuration-driven load generation subsystem built on Locust. Its objective is to provide reproducible, identical traffic patterns that will later exercise different autoscaling configurations under evaluation:
- Fixed replicas (`fixed`)
- Standard reactive Horizontal Pod Autoscaler (`hpa`)
- Predictive autoscaling (`predictive`)
- Guarded predictive autoscaling (`guarded`)

By defining traffic patterns in typed declarative YAML profiles and executing them through a headless, bounded runner, the framework ensures that differences observed across autoscaling controllers in future experiments stem from controller dynamics rather than traffic variability or stochastic load generation.

> [!IMPORTANT]
> **Non-Benchmark Classification**: In Phase 7A, the scenario framework was validated using offline dry-runs, local integration tests, and one bounded smoke run against the cluster workload. This smoke run is **not** a benchmark, capacity test, throughput measurement, or autoscaling comparison. No comparative experiments have been conducted.

---

## 2. Validation vs. Smoke Execution vs. Formal Experiments

| Classification | Purpose | Target Environment | Duration | Assertions / Outputs |
| :--- | :--- | :--- | :--- | :--- |
| **Offline Dry-Run** | Syntax, range validation, and stage calculation without networking | None (offline) | 0s (instant) | Stage schedule printed to console; validates schema constraints |
| **Local Integration Smoke** | Verifies runner subprocess lifecycle, Locust CSV outputs, and metadata creation | Local FastAPI TestClient / mock server | 1–3s | Metadata written, CSVs populated, subprocess exit code 0 |
| **Cluster Smoke Run** | Confirms live connectivity against the Kubernetes workload endpoint | `http://127.0.0.1:18000/work` via temporary port-forward | 15s (2 users, 1000 CPU iterations) | Proves operational readiness without triggering autoscaling or stressing pods |
| **Formal Experiments (Future Phase)** | Rigorous empirical evaluation across controllers under identical load | Live Kubernetes cluster with target controller active | 120–240s full profiles | Multi-replica telemetry recorded in TSDB; statistical analysis across repeated runs |

---

## 3. Configuration Contract & Schema

Scenario profiles are defined in YAML and parsed into strictly validated Pydantic models ([`load_testing/config.py`](file:///load_testing/config.py)).

### Root Schema (`ScenarioConfig`)

| Field | Type | Default | Safety Constraints / Description |
| :--- | :--- | :--- | :--- |
| `scenario_name` | `str` | *Required* | Non-empty slug identifying the scenario profile. |
| `description` | `str` | *Required* | Concise narrative explaining the traffic pattern and intent. |
| `total_duration_seconds` | `int` | *Required* | 1 to 900 seconds. Must exactly match the sum of stage durations. |
| `host` | `str` | `http://127.0.0.1:8000` | Target URL with `http://` or `https://`. Must resolve to localhost unless overridden. |
| `endpoint` | `str` | `/work` | Must equal `/work` for experimental scenarios. |
| `random_seed` | `int` | `42` | Deterministic pseudo-random seed. Booleans rejected. |
| `cpu_iterations` | `int` | `100000` | 0 to 400,000 synthetic compute iterations per request. |
| `delay_ms` | `int` | `20` | 0 to 1,000 milliseconds synthetic sleep delay per request. |
| `payload_size` | `int` | `50` | 0 to 1,000 bytes synthetic echo response size. |
| `request_timeout_seconds`| `float`| `5.0` | 0.1 to 30.0 seconds HTTP client timeout. |
| `stages` | `list[StageConfig]`| *Required* | Non-empty list of contiguous sequential stages. |

### Stage Schema (`StageConfig`)

| Field | Type | Safety Constraints / Description |
| :--- | :--- | :--- |
| `duration_seconds` | `int` | Must be strictly positive (> 0). Booleans rejected. |
| `target_users` | `int` | 0 to 50 concurrent simulated users. Zero allowed for cooldown. |
| `spawn_rate_per_second` | `float` | > 0 and <= 10.0 users spawned per second. |

### Strict Safety Rules
- **Boolean Rejection**: Explicitly prevents YAML booleans (`true`/`false`) from coercing to numeric fields (`0`/`1`).
- **Duration Sum Check**: Total duration must exactly equal the arithmetic sum of `stage.duration_seconds`.
- **Localhost Default**: Rejects external hosts (e.g. `http://example.com`) unless the caller explicitly passes `--allow-non-local-host`. Wildcard addresses (`0.0.0.0`, `*`) are strictly prohibited under all circumstances.
- **Extra Fields**: Unknown fields raise validation errors (`extra="forbid"`).

---

## 4. Scenario Profiles

Five deterministic scenario profiles are implemented in [`load_testing/scenarios/`](file:///load_testing/scenarios/):

### 1. Steady (`steady.yaml`)
*Purpose:* Baseline validation with constant, moderate request pressure.
*Total Duration:* 180s | *Max Users:* 12 | *Spawn Rate:* 2.0/s | *CPU Iterations:* 100,000 | *Delay:* 20ms | *Payload:* 50B

```text
 Users
  12 |             +------------------------+
   4 |    +--------+                        |
   0 +----+--------+------------------------+----+
     0   10        30                      150  180 Time (s)
```

| Stage | Name / Intent | Duration | Target Users | Spawn Rate | Cumulative Time |
| :---: | :--- | :---: | :---: | :---: | :---: |
| 1 | Warm-up | 30s | 4 | 1.0/s | 0s – 30s |
| 2 | Steady state | 120s | 12 | 2.0/s | 30s – 150s |
| 3 | Cooldown | 30s | 0 | 2.0/s | 150s – 180s |

---

### 2. Ramp (`ramp.yaml`)
*Purpose:* Gradual, staged increase in active concurrency to test reactive scaling thresholds.
*Total Duration:* 240s | *Max Users:* 16 | *Spawn Rate:* 1.0–2.0/s | *CPU Iterations:* 80,000 | *Delay:* 10ms | *Payload:* 30B

```text
 Users
  16 |                                   +--------------+
  12 |                    +--------------+              |
   8 |     +--------------+                             |
   4 | +---+                                            |
   0 +-+---+--------------+--------------+--------------+----+
     0 10  30             90            150            210  240 Time (s)
```

| Stage | Name / Intent | Duration | Target Users | Spawn Rate | Cumulative Time |
| :---: | :--- | :---: | :---: | :---: | :---: |
| 1 | Warm-up | 30s | 4 | 1.0/s | 0s – 30s |
| 2 | Step 1 | 60s | 8 | 1.0/s | 30s – 90s |
| 3 | Step 2 | 60s | 12 | 2.0/s | 90s – 150s |
| 4 | Peak plateau | 60s | 16 | 2.0/s | 150s – 210s |
| 5 | Cooldown | 30s | 0 | 2.0/s | 210s – 240s |

---

### 3. Periodic (`periodic.yaml`)
*Purpose:* Deterministic cyclic oscillation between peak and valley load.
*Total Duration:* 240s | *Max Users:* 14 | *Spawn Rate:* 2.0/s | *CPU Iterations:* 120,000 | *Delay:* 15ms | *Payload:* 40B

```text
 Users
  14 |            +---------+             +---------+
   6 |            |         +---------+   |         +---------+
   4 |    +-------+         |         |   |         |         |
   0 +----+-------+---------+---------+---+---------+---------+----+
     0   10       30        75       120 125       165       210  240 Time (s)
```

| Stage | Name / Intent | Duration | Target Users | Spawn Rate | Cumulative Time |
| :---: | :--- | :---: | :---: | :---: | :---: |
| 1 | Warm-up | 30s | 4 | 1.0/s | 0s – 30s |
| 2 | Cycle 1 Peak | 45s | 14 | 2.0/s | 30s – 75s |
| 3 | Cycle 1 Trough | 45s | 6 | 2.0/s | 75s – 120s |
| 4 | Cycle 2 Peak | 45s | 14 | 2.0/s | 120s – 165s |
| 5 | Cycle 2 Trough | 45s | 6 | 2.0/s | 165s – 210s |
| 6 | Cooldown | 30s | 0 | 2.0/s | 210s – 240s |

---

### 4. Sudden Increase (`sudden_increase.yaml`)
*Purpose:* Abrupt step-jump from low baseline to high demand to assess reactive lag vs. predictive pre-scaling.
*Total Duration:* 180s | *Max Users:* 18 | *Spawn Rate:* 5.0/s | *CPU Iterations:* 90,000 | *Delay:* 10ms | *Payload:* 20B

```text
 Users
  18 |                           +------------------------+
   4 |    +----------------------+                        |
   0 +----+----------------------+------------------------+----+
     0   10                      60                      150  180 Time (s)
```

| Stage | Name / Intent | Duration | Target Users | Spawn Rate | Cumulative Time |
| :---: | :--- | :---: | :---: | :---: | :---: |
| 1 | Low baseline | 60s | 4 | 1.0/s | 0s – 60s |
| 2 | Sudden surge | 90s | 18 | 5.0/s | 60s – 150s |
| 3 | Cooldown | 30s | 0 | 3.0/s | 150s – 180s |

---

### 5. Drift (`drift.yaml`)
*Purpose:* Structural shift in load duration and amplitude that invalidates earlier periodic assumptions.
*Total Duration:* 240s | *Max Users:* 18 | *Spawn Rate:* 2.0/s | *CPU Iterations:* 100,000 | *Delay:* 20ms | *Payload:* 30B

```text
 Users
  18 |                                            +--------------------+
  10 |                              +-------------+                    |
   6 |               +--------------+                                  |
   4 |    +----------+                                                 |
   0 +----+----------+--------------+-------------+--------------------+----+
     0   10          30             75           120                  210  240 Time (s)
```

| Stage | Name / Intent | Duration | Target Users | Spawn Rate | Cumulative Time |
| :---: | :--- | :---: | :---: | :---: | :---: |
| 1 | Warm-up | 30s | 4 | 1.0/s | 0s – 30s |
| 2 | Baseline low | 45s | 6 | 1.0/s | 30s – 75s |
| 3 | Moderate rise | 45s | 10 | 2.0/s | 75s – 120s |
| 4 | Sustained drift peak | 90s | 18 | 2.0/s | 120s – 210s |
| 5 | Cooldown | 30s | 0 | 2.0/s | 210s – 240s |

---

## 5. Locust User Behavior (`KubePredictUser`)

Defined in [`load_testing/locustfile.py`](file:///load_testing/locustfile.py):
- **User Lifecycle**:
  - `on_start()` queries `/healthz` once during worker startup to verify connectivity.
  - `@task` loops continuously, generating POST requests to `/work`.
- **Deterministic Payload**:
  - Each request includes a unique header `X-Request-ID: req-<uuid4>`.
  - JSON request body:
    ```json
    {
      "cpu_iterations": 100000,
      "delay_ms": 20,
      "payload_size": 50
    }
    ```
- **Pacing**: `wait_time = between(0.05, 0.20)` (bounded jitter between 50ms and 200ms).
- **Safety**:
  - Request timeout enforced via `request_timeout_seconds`.
  - Errors and HTTP status codes >= 400 recorded as failures in Locust stats without dumping response bodies or infinite retries.
  - No secret tokens, credentials, or sensitive headers are logged or transmitted.

---

## 6. Execution & CLI Runner

The framework is invoked headlessly via the project-local Python interpreter:
```powershell
.venv\Scripts\python.exe -m load_testing.runner `
    --scenario load_testing/scenarios/steady.yaml `
    --output-root experiments/output
```

### Supported CLI Flags
- `--scenario` *(required)*: Path to scenario YAML file or registered name (`steady`, `ramp`, `periodic`, `sudden_increase`, `drift`).
- `--output-root` *(required)*: Directory where unique `<run-id>` folders are created.
- `--dry-run`: Validates configuration, outputs the stage schedule, and exits without sending requests or writing output directories.
- `--host`: Overrides the target host (e.g. `http://127.0.0.1:18000`).
- `--allow-non-local-host`: Explicit opt-in required if targeting a non-loopback host.
- `--run-id`: Custom run identifier (must match `^[a-zA-Z0-9_\-]+$`).
- `--application-image`: Metadata tag for application container image.
- `--cluster-name`: Metadata tag for cluster name.
- `--controller-mode`: Metadata tag for controller baseline (`fixed`, `hpa`, `predictive`, `guarded`).

---

## 7. Output Directory Structure & Run Metadata

Outputs are isolated in `experiments/output/<run-id>/`:

```text
experiments/output/
└── smoke-20260909_183552/
    ├── metadata.json
    ├── run.log
    ├── locust_stats.csv
    ├── locust_stats_history.csv
    ├── locust_failures.csv
    └── locust_exceptions.csv
```

### Git Hygiene
- The repository `.gitignore` ignores `experiments/output/*` while preserving `!experiments/output/.gitkeep`.
- Raw load test outputs, CSV files, and run logs remain local and are never tracked in version control.
- Overwriting existing run directories is prohibited; the runner raises `FileExistsError` if a collision occurs.

### Run Metadata Schema (`metadata.json`)
The run metadata record ([`load_testing/metadata.py`](file:///load_testing/metadata.py)) records:
- `run_id`: Unique filesystem-safe identifier.
- `scenario_name`: Validated scenario name.
- `started_at` & `completed_at`: Timezone-aware ISO-8601 UTC timestamps.
- `status`: Lifecycle state (`started`, `completed`, `failed`, `interrupted`).
- `configuration_hash`: SHA-256 digest of the scenario configuration file for immutability auditing.
- `git_commit`: Current HEAD commit hash (or `"unknown"` if uncommitted).
- `git_dirty`: Boolean flag indicating uncommitted workspace changes.
- `locust_version` & `python_version`: Exact runtime tool versions.
- `stages`: Serialized stage definitions with user counts and spawn rates.
- `actual_requests`: Extracted total requests, failures, and RPS from Locust CSV output upon completion.

---

## 8. PowerShell Helper Scripts

### Offline Scenario Validation
Validates all 5 committed scenarios through runner dry-run without network calls or Kubernetes dependencies:
```powershell
.\scripts\load-testing\validate-scenarios.ps1
```

### Cluster Workload Smoke Test
Performs a safe, bounded 15-second connectivity check against the live Kubernetes workload:
```powershell
.\scripts\load-testing\smoke-test.ps1 -LocalPort 18000
```
- Verifies context (`kind-kubepredict-dev`) and 2 Ready replicas.
- Establishes a temporary port-forward to `service/kubepredict-workload-api`.
- Runs a micro-scenario (2 users, 15s duration, 1000 CPU iterations).
- Tears down the port-forward in a `finally` block and verifies socket release.

---

## 9. Current Limitations & Next Steps

- **No Autoscaling Controller**: Autoscaling controllers are not implemented. Scenarios run purely against the fixed baseline.
- **No Performance Conclusions**: Throughput, latency, and CPU numbers from smoke testing are strictly operational checks and must not be cited as performance claims.
- **Single-Host Target**: Distributed Locust master/worker topologies are not used; all load generation is local and bounded.
