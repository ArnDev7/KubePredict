# KubePredict

**Adaptive Predictive Autoscaling for Cloud-Native Applications Using Kubernetes**

A research-oriented university project comparing four autoscaling strategies for Kubernetes workloads:

1. **Fixed replicas** — static, manually configured pod count
2. **HPA baseline** — standard Kubernetes Horizontal Pod Autoscaler (reactive)
3. **Predictive** — forecast-driven scaling using offline-trained time-series models *(planned)*
4. **Guarded predictive** — prediction with confidence checks, burst override, drift handling, cooldown, bounded scaling, and reactive fallback *(planned)*

## Research Problem

Can a guarded predictive autoscaler reduce latency and SLO violations relative to default HPA without excessive replica usage or unstable scaling under steady, periodic, ramp, burst, and drift workloads?

## Current Status

| Component | Status |
|---|---|
| FastAPI workload service | ✅ Implemented |
| Docker container (multi-stage, non-root) | ✅ Implemented |
| Local Kubernetes cluster (kind) | ✅ Implemented |
| Fixed-replica baseline | ✅ Deployed & verified |
| Metrics Server | ✅ Deployed & verified |
| HPA baseline | ✅ Validated (scale-up/down observed) |
| Prometheus monitoring | ✅ Deployed & verified |
| Grafana dashboards | ✅ Deployed & verified |
| Request scenario framework (Locust) | ✅ Implemented (5 scenarios) |
| Telemetry capture pipeline | ✅ Implemented & validated |
| Offline forecasting pipeline | ✅ Implemented (4 model families) |
| Forecasting audit & model selection | ✅ Frozen model configuration |
| Observe-only controller | ✅ Implemented (read-only) |
| Replica calculation & scaling | 🔲 Planned (Phase 9B) |
| Comparative experiments | 🔲 Planned (Phase 10) |

> **Research constraint**: No claims of improved latency, resource efficiency, or prediction accuracy are made until experiments produce saved evidence. Planned behavior and measured results are clearly separated.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Kubernetes Cluster (kind)                     │
│                                                                 │
│  ┌──────────────────┐   ┌────────────┐   ┌──────────────────┐  │
│  │  workload-api     │   │ Prometheus │   │     Grafana      │  │
│  │  (FastAPI, 2 pods)│◄──│  (scrape)  │──►│  (dashboards)    │  │
│  └──────────────────┘   └─────┬──────┘   └──────────────────┘  │
│                               │                                 │
│  ┌──────────────────┐         │                                 │
│  │  Metrics Server   │         │                                 │
│  └──────────────────┘         │                                 │
└───────────────────────────────┼─────────────────────────────────┘
                                │
              ┌─────────────────▼─────────────────┐
              │     Observe-Only Controller        │
              │  (reads Prometheus, runs forecast, │
              │   logs predictions — no writes)    │
              └─────────────────┬─────────────────┘
                                │
              ┌─────────────────▼─────────────────┐
              │      Forecasting Engine            │
              │  (Persistence, MA, EWMA,           │
              │   Linear Trend — frozen config)    │
              └───────────────────────────────────┘
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full architecture specification.

## Technology Stack

| Layer | Technology |
|---|---|
| Application | Python 3.11+, FastAPI, Uvicorn |
| Metrics | prometheus-client, Prometheus `v2.54.1` |
| Visualization | Grafana OSS `11.2.0` |
| Container | Docker (multi-stage, non-root) |
| Orchestration | Kubernetes via kind |
| Load Testing | Locust |
| Forecasting | NumPy, pandas |
| Testing | pytest, Ruff, mypy |

## Repository Structure

```
app/                    # FastAPI workload service
config/                 # Type-safe settings and example configuration
controller/             # Observe-only controller (Phase 9A)
forecasting/            # Offline forecasting engine and frozen model config
shared/                 # Shared enums, Pydantic models, validation
load_testing/           # Locust scenario framework and YAML profiles
experiments/            # Telemetry capture pipeline and experiment configs
kubernetes/
  base/                 # Core Kustomize manifests (Namespace, Deployment, Service)
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
  load-testing/         # Scenario validation and smoke tests
  experiments/          # Telemetry capture scripts
  forecasting/          # Forecasting evaluation and audit scripts
  controller/           # Observer controller scripts
tests/
  unit/                 # Unit tests
  integration/          # Integration tests
docs/                   # Architecture, operations, and methodology docs
```

## Prerequisites

- **Python** ≥ 3.11, < 3.15
- **Docker Desktop** with Kubernetes support
- **kubectl** CLI
- **kind** (Kubernetes IN Docker)
- **Git**

## Local Python Setup

```powershell
# Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install runtime + development dependencies
pip install -e ".[dev]"
```

## Docker

Build and run the workload service container:

```powershell
.\scripts\docker\build.ps1           # Build kubepredict-workload-api:0.1.0
.\scripts\docker\run.ps1             # Run container locally
.\scripts\docker\smoke-test.ps1      # Verify health and endpoints
.\scripts\docker\stop.ps1            # Stop and remove container
```

- **Image**: `kubepredict-workload-api:0.1.0`
- **Base**: `python:3.14-slim` (multi-stage, non-root)

See [docs/DOCKER.md](docs/DOCKER.md) for details.

## Kubernetes Setup

### Cluster Creation

```powershell
.\scripts\kubernetes\check-prerequisites.ps1    # Verify Docker, kubectl, kind
.\scripts\kubernetes\create-cluster.ps1          # Create kind cluster
.\scripts\kubernetes\load-image.ps1              # Load image into kind nodes
.\scripts\kubernetes\deploy-fixed.ps1            # Deploy fixed 2-replica baseline
```

- **Cluster**: `kubepredict-dev` (single control-plane node)
- **Context**: `kind-kubepredict-dev`
- **Namespace**: `kubepredict-system`
- **Deployment**: `kubepredict-workload-api` (2 fixed replicas)

```powershell
.\scripts\kubernetes\inspect-fixed.ps1           # Inspect deployment state
.\scripts\kubernetes\port-forward.ps1 -LocalPort 8000   # Port-forward to localhost
.\scripts\kubernetes\smoke-endpoints.ps1         # Smoke-test endpoints
```

See [docs/baselines/FIXED_REPLICA_BASELINE.md](docs/baselines/FIXED_REPLICA_BASELINE.md).

### Metrics Server

```powershell
.\scripts\kubernetes\install-metrics-server.ps1  # Deploy Metrics Server v0.7.2
.\scripts\kubernetes\inspect-metrics-server.ps1  # Verify resource metrics API
```

See [docs/METRICS_SERVER.md](docs/METRICS_SERVER.md).

### Prometheus

```powershell
.\scripts\kubernetes\deploy-prometheus.ps1       # Deploy to kubepredict-monitoring
.\scripts\kubernetes\port-forward-prometheus.ps1 -LocalPort 9090
.\scripts\kubernetes\validate-prometheus-scrape.ps1
```

See [docs/PROMETHEUS.md](docs/PROMETHEUS.md).

### Grafana

```powershell
.\scripts\kubernetes\deploy-grafana.ps1          # Deploy with auto-provisioned datasource
.\scripts\kubernetes\port-forward-grafana.ps1 -LocalPort 3000
.\scripts\kubernetes\inspect-grafana.ps1
```

- Dashboard: KubePredict Overview (18 panels, 5 rows)
- Credentials: Generated at runtime via Kubernetes Secret

See [docs/GRAFANA.md](docs/GRAFANA.md).

### HPA Baseline

```powershell
.\scripts\kubernetes\deploy-hpa.ps1              # Apply autoscaling/v2 HPA
.\scripts\kubernetes\inspect-hpa.ps1             # Check HPA status
.\scripts\kubernetes\remove-hpa.ps1 -ConfirmHpaRemoval   # Remove HPA safely
```

See [docs/baselines/HPA_BASELINE.md](docs/baselines/HPA_BASELINE.md).

## Request Scenarios

Five deterministic, configuration-driven traffic profiles built with Locust:

| Scenario | Duration | Users | Pattern |
|---|---|---|---|
| `steady` | 180s | 12 | Constant moderate load |
| `ramp` | 240s | 16 | Step-wise increase |
| `periodic` | 240s | 14 | Repeated high/low cycles |
| `sudden_increase` | 180s | 18 | Abrupt step-change |
| `drift` | 240s | 18 | Changed duration and amplitude |

```powershell
.\scripts\load-testing\validate-scenarios.ps1        # Offline dry-run validation
.\scripts\load-testing\smoke-test.ps1 -LocalPort 18000  # 15s bounded smoke test
```

See [docs/REQUEST_SCENARIOS.md](docs/REQUEST_SCENARIOS.md).

## Telemetry Capture

Aligned Prometheus telemetry and Kubernetes resource metrics captured during scenario execution:

```powershell
.\scripts\experiments\validate-telemetry-plan.ps1    # Validate query definitions
.\scripts\experiments\capture-scenario.ps1 -Scenario steady
.\scripts\experiments\capture-all-scenarios.ps1      # All 5 scenarios
.\scripts\experiments\inspect-dataset.ps1 -Scenario steady
```

Datasets are saved to `data/raw/scenarios/` (git-ignored). See [docs/TELEMETRY_DATASET.md](docs/TELEMETRY_DATASET.md).

## Offline Forecasting

Model-independent forecasting pipeline evaluated against captured telemetry:

- **Models**: Persistence (naive), Moving Average, EWMA, Linear Trend
- **Target**: `request_rate_per_second` at 1/2/4-step horizons (15s/30s/60s)
- **Split**: 60% train / 20% validation / 20% test (strict chronological)
- **Metrics**: MAE (primary), RMSE, sMAPE, Bias
- **Frozen config**: `forecasting/config/final_forecaster.yaml`

```powershell
.\scripts\forecasting\validate-data.ps1              # Validate datasets offline
.\scripts\forecasting\evaluate-baselines.ps1         # Run evaluation
.\scripts\forecasting\audit-selection.ps1            # Audit leakage and robustness
.\scripts\forecasting\validate-final-model.ps1       # Validate frozen model
```

See [docs/FORECASTING.md](docs/FORECASTING.md) and [docs/FORECAST_SELECTION.md](docs/FORECAST_SELECTION.md).

## Observe-Only Controller

Read-only controller connecting the frozen forecaster to live Prometheus telemetry:

- Reads request-rate history from Prometheus (fixed PromQL query)
- Invokes `ForecastingService` to produce predictions
- Records pending forecasts, matches future actuals, computes residuals
- **Zero Kubernetes write capability** (verified by static AST audit)
- `ENABLE_LIVE_SCALING=false`, `CONTROLLER_MODE=observe`

```powershell
.\scripts\controller\validate-observer.ps1           # Validate configuration
.\scripts\controller\run-observer.ps1                # Start observe-only loop
.\scripts\controller\inspect-observer-run.ps1        # Inspect observation results
```

See [docs/OBSERVE_ONLY_CONTROLLER.md](docs/OBSERVE_ONLY_CONTROLLER.md).

## Testing and Quality

```powershell
# Run all tests
.venv\Scripts\python.exe -m pytest

# Lint
.venv\Scripts\python.exe -m ruff check .

# Format check
.venv\Scripts\python.exe -m ruff format --check .

# Type check
.venv\Scripts\python.exe -m mypy app controller forecasting config shared load_testing experiments tests

# Or use Makefile shortcuts
make test
make lint
make format-check
make type-check
```

## Security and Safe Defaults

- `ENABLE_LIVE_SCALING=false` — scaling is disabled by default
- `CONTROLLER_MODE=observe` — controller only observes, never writes
- Container runs as non-root with read-only filesystem
- Resource requests and limits on all pods
- Grafana credentials generated at runtime via Kubernetes Secret
- No credentials, tokens, or secrets stored in the repository
- Static write-capability audit verifies zero Kubernetes write imports

## Generated Data Policy

All runtime-generated data is git-ignored and stored locally:

| Directory | Contents |
|---|---|
| `data/raw/` | Raw telemetry datasets from scenario captures |
| `data/processed/` | Forecasting evaluation outputs |
| `experiments/output/` | Locust run outputs |
| `results/` | Figures, raw results, summaries |

These directories are preserved in the repository structure via `.gitkeep` files but their contents are never committed. Data can be reproduced by re-running the capture and evaluation pipelines.

## Limitations

- Predictive scaling decisions and replica calculation are not yet implemented
- Forecast guard, burst detection, and reactive fallback are planned (Phase 9B)
- No live scaling actuator exists — the controller is strictly observe-only
- Comparative experiments between strategies have not been conducted
- The project uses a local kind cluster — not a production environment
- Forecasting models are lightweight baselines; no deep learning models are included

## Team

Arnav Tiwari and team

## References

- [Kubernetes HPA Documentation](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
- [Prometheus Documentation](https://prometheus.io/docs/)
- [Grafana Documentation](https://grafana.com/docs/)
- [Locust Documentation](https://docs.locust.io/)
- [kind Documentation](https://kind.sigs.k8s.io/)

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
