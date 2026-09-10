# Stable Project Scope

## Project title
Adaptive Predictive Autoscaling for Cloud-Native Applications Using Kubernetes

## Research question
Can a guarded predictive autoscaler reduce latency and SLO violations relative to default HPA without excessive replica usage or unstable scaling under steady, periodic, ramp, burst, and drift workloads?

## Required components
- Stateless FastAPI workload service
- Prometheus-compatible metrics endpoint
- Reproducible Docker image
- Local Kubernetes deployment
- Fixed-replica baseline
- HPA baseline
- Workload generator with deterministic scenarios
- Telemetry collection
- Lightweight workload forecaster
- Predictive scaling policy
- Confidence guard
- Burst detector
- Reactive fallback
- Cooldown and bounded replica changes
- Repeatable experiment runner
- CSV/JSON evidence and figures
- Unit, smoke, and integration tests

## Out of scope until core project passes
- Reinforcement learning
- LSTM or transformer forecasting
- GPU scheduling
- Multi-cluster federation
- Cloud billing optimization
- Production security certification
- Public cloud deployment

## No-fabrication rule
The repository must never contain invented benchmark numbers. Documentation must label unexecuted commands, planned experiments, and expected outcomes accurately.
