# KubePredict

**Adaptive Predictive Autoscaling for Cloud-Native Applications Using Kubernetes**

A research-oriented university project that designs, implements, and compares four autoscaling strategies for Kubernetes workloads, culminating in a fully deployed, in-cluster predictive controller with trust-guarded forecasting, reactive fallback, and policy arbitration.

1. **Fixed replicas** — static, manually configured pod count (baseline)
2. **HPA baseline** — standard Kubernetes Horizontal Pod Autoscaler (reactive)
3. **Predictive** — forecast-driven scaling using offline-trained time-series models
4. **Guarded predictive** — prediction with confidence checks, burst override, drift handling, cooldown, bounded scaling, and reactive fallback

## Research Problem

Can a guarded predictive autoscaler reduce latency and SLO violations relative to default HPA without excessive replica usage or unstable scaling under steady, periodic, ramp, burst, and drift workloads?

## Project Status

| Component | Status |
|---|---|
| FastAPI workload service | ✅ Complete |
| Docker container (multi-stage, non-root) | ✅ Complete |
| Local Kubernetes cluster (kind) | ✅ Complete |
| Fixed-replica baseline | ✅ Deployed & verified |
| Metrics Server | ✅ Deployed & verified |
| HPA baseline | ✅ Validated (scale-up/down observed) |
| Prometheus monitoring | ✅ Deployed & verified |
| Grafana dashboards | ✅ Deployed & verified |
| Request scenario framework (Locust) | ✅ Implemented (5 scenarios) |
| Telemetry capture pipeline | ✅ Implemented & validated |
| Offline forecasting pipeline | ✅ Implemented (4 model families) |
| Forecasting audit & model selection | ✅ Frozen model configuration |
| Observe-only controller | ✅ Implemented & deployed |
| Dry-run capacity model & recommendations | ✅ Implemented & deployed |
| Forecast trust guard & guarded dry-run | ✅ Implemented & deployed |
| Rapid-change detection & reactive fallback | ✅ Implemented & deployed |
| Predictive-reactive arbitration & dampening | ✅ Implemented & deployed |
| In-cluster controller Deployment & RBAC | ✅ Deployed (dry-run mode) |
| Demo scripts & documentation | ✅ Complete |
| Test suite | ✅ 491 tests passing |

> **Research constraint**: No claims of improved latency, resource efficiency, or prediction accuracy are made until formal comparative experiments produce saved evidence. Controller currently operates in **dry-run mode** — it observes and recommends but does not write to the Kubernetes API.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                   Kubernetes Cluster (kind-kubepredict-dev)        │
│                                                                    │
│  ┌───────────────────┐   ┌────────────┐   ┌────────────────────┐  │
│  │  workload-api      │   │ Prometheus │   │      Grafana       │  │
│  │  (FastAPI, 2 pods) │◄──│  (scrape)  │──►│  (dashboards)      │  │
│  │                    │   └─────┬──────┘   └────────────────────┘  │
│  │  POST /work        │         │                                  │
│  │  GET  /healthz     │         │                                  │
│  │  GET  /metrics     │         │                                  │
│  └───────────────────┘         │                                  │
│                                 │                                  │
│  ┌───────────────────┐          │                                  │
│  │   Metrics Server  │          │                                  │
│  └───────────────────┘          │                                  │
│                                 ▼                                  │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              KubePredict Controller (in-cluster)             │  │
│  │                                                              │  │
│  │  1. Read request-rate history from Prometheus (PromQL)       │  │
│  │  2. Run frozen forecasting model (EWMA / Linear Trend)       │  │
│  │  3. Evaluate trust guard (MAE, sMAPE, residual window)       │  │
│  │  4. Detect rapid-change surge                                │  │
│  │  5. Arbitrate: predictive / reactive / rapid-override        │  │
│  │  6. Apply bounds, step limits, cooldown, stabilisation       │  │
│  │  7. Log recommendation — DRY-RUN (no Kubernetes writes)      │  │
│  │                                                              │  │
│  │  CONTROLLER_MODE=dry_run   ENABLE_LIVE_SCALING=false         │  │
│  └──────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full specification.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Application | Python 3.14, FastAPI, Uvicorn |
| Metrics | prometheus-client, Prometheus v2.54.1 |
| Visualization | Grafana OSS 11.2.0 |
| Container | Docker (multi-stage, non-root, `python:3.14-slim`) |
| Orchestration | Kubernetes via kind |
| Load Testing | Locust |
| Forecasting | NumPy, pandas |
| Testing | pytest (491 tests), Ruff, mypy |

---

## Repository Structure

```
app/                    # FastAPI workload service
config/                 # Type-safe settings (pydantic-settings)
controller/             # In-cluster controller — observer, dry-run, trust guard,
│                       #   rapid-change, reactive fallback, arbitration
│   config/             # Controller YAML configs (capacity, policy, trust guard)
forecasting/            # Offline forecasting engine and frozen model config
shared/                 # Shared enums, Pydantic models, validation
load_testing/           # Locust scenario framework and YAML profiles
experiments/            # Telemetry capture pipeline and experiment configs
kubernetes/
  base/                 # Core Kustomize manifests (Namespace, Deployment, Service)
  controller/           # Controller Deployment, RBAC, ConfigMap, Service
  kind/                 # Kind cluster topology
  metrics-server/       # Metrics Server with kind patch
  hpa/                  # HPA baseline overlay
  monitoring/
    prometheus/         # Prometheus deployment and scrape config
    grafana/            # Grafana deployment, datasource, dashboard
monitoring/
  grafana/dashboards/   # Canonical dashboard JSON (source of truth)
scripts/
  docker/               # Docker build, run, smoke-test, inspect, stop
  kubernetes/           # Cluster and component lifecycle scripts
  controller/           # Controller build, deploy, run, inspect scripts
  demo/                 # Demo helper scripts (start, load, stop, rehearse)
  load-testing/         # Scenario validation and smoke tests
  experiments/          # Telemetry capture scripts
  forecasting/          # Forecasting evaluation and audit scripts
tests/
  unit/                 # Unit tests
  integration/          # Integration tests
docs/                   # Architecture, operations, and methodology docs
```

---

## Prerequisites

- **Python** ≥ 3.11, < 3.15
- **Docker Desktop**
- **kubectl** CLI
- **kind** (Kubernetes IN Docker)
- **Git**

---

## Quick Start

### 1. Python environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### 2. Run the workload API locally (no Kubernetes needed)

```powershell
.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8080 --reload
```

Open **http://127.0.0.1:8080/docs** — interactive Swagger UI.

### 3. Full demo (Kubernetes cluster required)

```powershell
# In one persistent PowerShell window — keep it open
.\scripts\demo\start-demo.ps1   # starts port-forwards: API 8000, Prom 9090, Grafana 3000

# In a second window
.\scripts\demo\load-demo.ps1    # sends bounded traffic through all endpoints

# When done
.\scripts\demo\stop-demo.ps1    # kills port-forwards, cleans .demo-state/
```

| URL | What you see |
|-----|-------------|
| `http://127.0.0.1:8080/docs` | Interactive API (Swagger UI) |
| `http://127.0.0.1:9090` | Prometheus — query live metrics |
| `http://127.0.0.1:9090/targets` | Scrape targets (2 workload pods should be UP) |
| `http://127.0.0.1:3000` | Grafana — **KubePredict Overview** dashboard (login: `admin/admin`) |

See [docs/DEMO_RUNBOOK.md](docs/DEMO_RUNBOOK.md) for the full step-by-step guide and [docs/DEMO_VIDEO_SCRIPT.md](docs/DEMO_VIDEO_SCRIPT.md) for the narration script.

---

## Docker

```powershell
.\scripts\docker\build.ps1           # Build kubepredict-workload-api:0.1.0
.\scripts\docker\run.ps1             # Run container locally
.\scripts\docker\smoke-test.ps1      # Verify health and endpoints
.\scripts\docker\stop.ps1            # Stop and remove container
```

See [docs/DOCKER.md](docs/DOCKER.md).

---

## Kubernetes Setup

### Cluster

```powershell
.\scripts\kubernetes\check-prerequisites.ps1
.\scripts\kubernetes\create-cluster.ps1         # Creates kind-kubepredict-dev
.\scripts\kubernetes\load-image.ps1
.\scripts\kubernetes\deploy-fixed.ps1           # Deploy 2-replica fixed baseline
```

- **Cluster**: `kubepredict-dev` | **Context**: `kind-kubepredict-dev`
- **Namespace**: `kubepredict-system`

### Metrics Server

```powershell
.\scripts\kubernetes\install-metrics-server.ps1
.\scripts\kubernetes\inspect-metrics-server.ps1
```

See [docs/METRICS_SERVER.md](docs/METRICS_SERVER.md).

### Prometheus

```powershell
.\scripts\kubernetes\deploy-prometheus.ps1
.\scripts\kubernetes\port-forward-prometheus.ps1 -LocalPort 9090
.\scripts\kubernetes\validate-prometheus-scrape.ps1
```

See [docs/PROMETHEUS.md](docs/PROMETHEUS.md).

### Grafana

```powershell
.\scripts\kubernetes\deploy-grafana.ps1
.\scripts\kubernetes\port-forward-grafana.ps1 -LocalPort 3000
.\scripts\kubernetes\inspect-grafana.ps1
```

- Dashboard: **KubePredict Overview** (18 panels, 5 rows)

See [docs/GRAFANA.md](docs/GRAFANA.md).

### HPA Baseline

```powershell
.\scripts\kubernetes\deploy-hpa.ps1
.\scripts\kubernetes\inspect-hpa.ps1
.\scripts\kubernetes\remove-hpa.ps1 -ConfirmHpaRemoval
```

See [docs/baselines/HPA_BASELINE.md](docs/baselines/HPA_BASELINE.md).

---

## Request Scenarios

Five deterministic, configuration-driven traffic profiles built with Locust:

| Scenario | Duration | Users | Pattern |
|---|---|---|---|
| `steady` | 180 s | 12 | Constant moderate load |
| `ramp` | 240 s | 16 | Step-wise increase |
| `periodic` | 240 s | 14 | Repeated high/low cycles |
| `sudden_increase` | 180 s | 18 | Abrupt step-change |
| `drift` | 240 s | 18 | Changed duration and amplitude |

```powershell
.\scripts\load-testing\validate-scenarios.ps1
.\scripts\load-testing\smoke-test.ps1 -LocalPort 18000
```

See [docs/REQUEST_SCENARIOS.md](docs/REQUEST_SCENARIOS.md).

---

## Telemetry Capture

```powershell
.\scripts\experiments\validate-telemetry-plan.ps1
.\scripts\experiments\capture-scenario.ps1 -Scenario steady
.\scripts\experiments\capture-all-scenarios.ps1
.\scripts\experiments\inspect-dataset.ps1 -Scenario steady
```

Datasets are saved to `data/raw/scenarios/` (git-ignored). See [docs/TELEMETRY_DATASET.md](docs/TELEMETRY_DATASET.md).

---

## Offline Forecasting

Four model families evaluated at 1/2/4-step horizons (15 s / 30 s / 60 s):

| Model | Type |
|---|---|
| Persistence | Naïve last-value baseline |
| Moving Average | Simple sliding window |
| EWMA | Exponentially weighted — **selected** |
| Linear Trend | Ordinary least squares |

- **Split**: 60 % train / 20 % validation / 20 % test (strict chronological)
- **Primary metric**: MAE; also RMSE, sMAPE, signed bias
- **Frozen config**: `forecasting/config/final_forecaster.yaml`

```powershell
.\scripts\forecasting\validate-data.ps1
.\scripts\forecasting\evaluate-baselines.ps1
.\scripts\forecasting\audit-selection.ps1
.\scripts\forecasting\validate-final-model.ps1
```

See [docs/FORECASTING.md](docs/FORECASTING.md) and [docs/FORECAST_SELECTION.md](docs/FORECAST_SELECTION.md).

---

## In-Cluster Controller

The controller is deployed as a Kubernetes Deployment in `kubepredict-system` and runs a full pipeline every 15 seconds:

```
Prometheus ──► Forecasting Engine ──► Trust Guard ──► Arbitration ──► Log Decision
                                                                    (no K8s writes)
```

### Pipeline stages

| Stage | What it does |
|---|---|
| **Observer** | Reads `request_rate_per_second` history via fixed PromQL query |
| **Forecasting** | Runs frozen EWMA/Linear-Trend model; produces point forecast |
| **Trust Guard** | Checks residual MAE ≤ 10 req/s, sMAPE ≤ 50 % — blocks untrusted forecasts |
| **Rapid-Change Detector** | Detects surges (ratio ≥ 2×, Δr ≥ 5 req/s) over sliding window |
| **Capacity Model** | Converts forecast → required replicas (15.75 req/s/replica, bounds [1,10]) |
| **Reactive Fallback** | HPA-style proportional fallback when forecast untrusted |
| **Arbitration** | Priority: rapid-override → predictive → reactive → no-recommendation |
| **Dampening** | 60 s cooldown, 90 s downscale stabilisation, ±3/−1 step limits |

### Controller scripts

```powershell
.\scripts\controller\build-controller.ps1        # Build & load controller image
.\scripts\controller\deploy-controller.ps1       # Deploy to kubepredict-system
.\scripts\controller\inspect-live-controller.ps1 # Show live pod logs & ConfigMap
.\scripts\controller\run-dry-run.ps1             # Run a bounded local dry-run loop
.\scripts\controller\run-guarded-dry-run.ps1     # With trust guard
.\scripts\controller\run-arbitration-dry-run.ps1 # Full arbitration pipeline
.\scripts\controller\inspect-dry-run.ps1         # Inspect decision artifacts
.\scripts\controller\inspect-arbitration-run.ps1 # Inspect arbitration evidence
.\scripts\controller\enable-live-validation.ps1  # ⚠️ Enable live scaling (guarded)
.\scripts\controller\disable-live-scaling.ps1    # Restore safe defaults
.\scripts\controller\remove-controller.ps1       # Remove from cluster
```

See [docs/DRY_RUN_CONTROLLER.md](docs/DRY_RUN_CONTROLLER.md), [docs/FORECAST_TRUST_GUARD.md](docs/FORECAST_TRUST_GUARD.md), and [docs/ARBITRATION_POLICY.md](docs/ARBITRATION_POLICY.md).

---

## Demo

```powershell
# Start all port-forwards (keep this window open)
.\scripts\demo\start-demo.ps1

# Generate bounded traffic
.\scripts\demo\load-demo.ps1

# Full rehearsal (dry-run by default)
.\scripts\demo\rehearse-demo.ps1

# Optional live-scaling rehearsal (requires explicit flag)
.\scripts\demo\rehearse-demo.ps1 -ConfirmLiveScalingDemo

# Stop and clean up
.\scripts\demo\stop-demo.ps1
```

Docs: [DEMO_RUNBOOK.md](docs/DEMO_RUNBOOK.md) · [DEMO_VIDEO_SCRIPT.md](docs/DEMO_VIDEO_SCRIPT.md) · [DEMO_COMMANDS.md](docs/DEMO_COMMANDS.md)

---

## Testing & Quality

```powershell
.venv\Scripts\python.exe -m pytest               # 491 tests
.venv\Scripts\python.exe -m ruff check .         # Lint
.venv\Scripts\python.exe -m ruff format --check .# Format check
.venv\Scripts\python.exe -m mypy app controller forecasting config shared load_testing experiments tests
```

Or via Makefile:

```powershell
make test
make lint
make format-check
make type-check
```

---

## Security & Safe Defaults

- `ENABLE_LIVE_SCALING=false` — live scaling disabled by default
- `CONTROLLER_MODE=dry_run` — controller only observes and recommends; never writes
- Container runs as non-root (`appuser`) with read-only filesystem
- Resource requests and limits on all pods
- Grafana credentials generated at runtime via Kubernetes Secret
- No secrets, tokens, or credentials stored in the repository
- Static AST audit verifies zero Kubernetes write-API imports in controller modules

---

## Generated Data Policy

All runtime-generated data is git-ignored:

| Directory | Contents |
|---|---|
| `data/raw/` | Raw telemetry datasets |
| `data/processed/` | Forecasting evaluation outputs |
| `experiments/output/` | Controller and Locust run outputs |
| `results/` | Figures, raw results, summaries |
| `.demo-state/` | Temporary demo port-forward PIDs (cleaned on stop) |

---

## Limitations

- Controller operates in **dry-run mode only** — live scaling actuation requires explicit opt-in via `enable-live-validation.ps1` and is guarded by multiple pre-condition checks
- Formal comparative experiments (steady, ramp, periodic, burst, drift across all four modes) have not yet been run — no performance claims are made
- Local `kind` cluster only — not a production cloud environment
- Forecasting models are lightweight statistical baselines; no deep learning models included
- Grafana screenshots cannot be captured programmatically — manual screen recording is required for the demo

---

## Team

Arnav Tiwari and team

---

## References

- [Kubernetes HPA Documentation](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
- [Prometheus Documentation](https://prometheus.io/docs/)
- [Grafana Documentation](https://grafana.com/docs/)
- [Locust Documentation](https://docs.locust.io/)
- [kind Documentation](https://kind.sigs.k8s.io/)

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
