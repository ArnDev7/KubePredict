# Acceptance Criteria

## Application
- Health, readiness, work, and metrics endpoints operate as documented. `[VERIFIED: Phase 2, Phase 4C]`
- Work endpoint supports configurable load within explicit safety bounds. `[VERIFIED: Phase 2, Phase 4C]`
- Tests cover normal and invalid inputs. `[VERIFIED: Phase 2, Phase 4C]`

## Containers and Kubernetes
- Docker image builds reproducibly. `[VERIFIED: Phase 3]`
- Kubernetes manifests validate and deploy to the selected local cluster. `[VERIFIED: Phase 4B, Phase 4C]`
- Resource requests and limits are present. `[VERIFIED: Phase 4B, Phase 4C]`
- Versioned HPA configuration exists (`autoscaling/v2`) with explicit scale target, replica bounds (1-6), 60% CPU target, and conservative behavior policies. `[VERIFIED: Phase 5B]`
- HPA manifest tests pass, Kustomize overlay renders deterministically, and server-side dry-run succeeds without mutating the cluster. `[VERIFIED: Phase 5B]`
- Fixed and HPA modes can be selected without editing unrelated files. `[VERIFIED: Phase 5C]`
- HPA runtime metrics resolution and scaling behavior. `[VERIFIED: Phase 5C]`

## Monitoring
- Kubernetes Metrics Server supplies node and pod resource metrics via the metrics.k8s.io API. `[VERIFIED: Phase 5A]`
- Prometheus scrapes application and self-monitoring metrics via Kubernetes pod discovery. `[VERIFIED: Phase 6A]`
- Grafana visualizes metrics via real-time dashboards. `[VERIFIED: Phase 6B]`
- Experiment evidence can be exported without relying only on screenshots. `[VERIFIED: Phase 7B - aligned Prometheus & k8s telemetry exported to validated CSV and JSON datasets]`

## Forecasting
- Forecast interface is model-independent. `[VERIFIED: Phase 8A - BaseForecaster abstract base class]`
- A naive baseline and at least one lightweight model are evaluated offline. `[VERIFIED: Phase 8A - Persistence, Moving Average, EWMA, and Linear Trend evaluated offline across 5 scenarios]`
- Metrics and train/test separation are documented. `[VERIFIED: Phase 8A - 60/20/20 chronological splits, MAE/RMSE/sMAPE/Bias, rolling-origin evaluation in docs/FORECASTING.md]`
- Temporal leakage audit and behavioral leakage traps pass. `[VERIFIED: Phase 8B - 6 behavioral traps passed, zero temporal leakage, forecast origin strictly precedes targets]`
- Independent metric reproduction and pipeline reproducibility verified. `[VERIFIED: Phase 8B - Clean-room metric recomputation matched within 1e-4; 100% deterministic re-execution]`
- Final frozen model configuration and runtime forecast contract implemented. `[VERIFIED: Phase 8B - forecasting/config/final_forecaster.yaml with status offline_selected; ForecastingService wrapper with persistence fallback in forecasting/service.py; documented in docs/FORECAST_SELECTION.md]`

## Controller
- Observe-only controller connects frozen forecaster to live Prometheus telemetry without Kubernetes writes. `[VERIFIED: Phase 9A - ObserverConfig, LiveTelemetryReader, ForecastStore, operational metrics, documented in docs/OBSERVE_ONLY_CONTROLLER.md]`
- Static write-capability audit verifies zero Kubernetes write capability in controller runtime. `[VERIFIED: Phase 9A - controller/audit.py AST analysis confirms no K8s client imports or write calls]`
- Future actual matching and residual calculation verified for empirical guard evidence. `[VERIFIED: Phase 9A - pending forecasts resolved strictly after target timestamps; signed residuals recorded outside Git]`
- Dry-run mode is implemented before live scaling. `[PLANNED: Phase 9B]`
- Scaling decisions contain reason codes and structured logs. `[PLANNED: Phase 9B]`
- Replica bounds, cooldown, bounded step size, and fallback are tested. `[PLANNED: Phase 9B]`
- Kubernetes permissions follow least privilege. `[PLANNED: Phase 9B]`

## Experiments & Scenario Generation
- Identical, deterministic workload scenarios exist to drive controllers under identical traffic patterns. `[VERIFIED: Phase 7A - 5 deterministic profiles: steady, ramp, periodic, sudden_increase, drift]`
- Configuration, stage definitions, and random seed are saved with every run. `[VERIFIED: Phase 7A - run metadata records seed, stage schedule, and config SHA-256]`
- Raw results remain immutable in dedicated run directories. `[VERIFIED: Phase 7A - unique run directories rejected if existing, metadata tracks execution status]`
- Generated summaries can be traced to raw evidence. `[VERIFIED: Phase 7A - metadata extracts actual request totals directly from raw Locust CSV outputs]`
- Validated telemetry capture pipeline records aligned Prometheus metrics (15s step) and Kubernetes state under identical request scenarios. `[VERIFIED: Phase 7B - 5 validated datasets in data/raw/scenarios/]`
- Telemetry datasets pass strict structural, temporal, and semantic validation rules without data loss or dropped requests. `[VERIFIED: Phase 7B - 58,559 total requests, 0 failures, 100% validation pass]`
- Formal comparative controller experiments across baselines. `[PLANNED: Phase 8]`

## Completion gate
A phase is complete only when Antigravity reports commands executed, test results, changed files, limitations, and the exact next phase. Passing syntax checks alone is insufficient.
