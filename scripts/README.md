# KubePredict Scripts Catalog

Idempotent, cross-platform PowerShell scripts for managing the lifecycle of KubePredict components.

All scripts adhere to strict safety invariants:
- **Context boundary**: Kubernetes scripts operate exclusively within the target context (`kind-kubepredict-dev`) and namespaces (`kubepredict-system`, `kubepredict-monitoring`).
- **Destructive confirmation**: Deletion and tear-down scripts require explicit `-Confirm` switches.
- **Fail-fast**: Scripts validate preconditions and dependencies before executing.

---

## Directory Overview

| Directory | Scope | Purpose |
|---|---|---|
| [`scripts/docker/`](#docker-scripts) | Container | Image building, local container testing, lifecycle management |
| [`scripts/kubernetes/`](#kubernetes-scripts) | Orchestration | Cluster creation, manifest deployment, service inspection, port-forwarding |
| [`scripts/load-testing/`](#load-testing-scripts) | Traffic | Scenario profile validation and Locust load generation |
| [`scripts/experiments/`](#experiments-scripts) | Telemetry | End-to-end telemetry capture and dataset inspection |
| [`scripts/forecasting/`](#forecasting-scripts) | ML / Forecasting | Data validation, baseline evaluation, audit, model verification |
| [`scripts/controller/`](#controller-scripts) | Controller | Observe-only controller validation, execution, and audit inspection |

---

## Docker Scripts (`scripts/docker/`)

| Script | Purpose | Example Usage |
|---|---|---|
| `build.ps1` | Build the canonical `kubepredict-workload-api:0.1.0` image | `.\scripts\docker\build.ps1` |
| `run.ps1` | Run the container locally with mapped ports | `.\scripts\docker\run.ps1` |
| `smoke-test.ps1` | Verify health and metrics endpoints on local container | `.\scripts\docker\smoke-test.ps1` |
| `inspect.ps1` | Inspect container status, resource usage, and logs | `.\scripts\docker\inspect.ps1` |
| `stop.ps1` | Stop and remove the local container | `.\scripts\docker\stop.ps1` |

## Kubernetes Scripts (`scripts/kubernetes/`)

### Cluster & Base Setup
| Script | Purpose |
|---|---|
| `check-prerequisites.ps1` | Verify Docker, kubectl, kind, and image availability |
| `create-cluster.ps1` | Provision kind cluster (`kubepredict-dev`) |
| `delete-cluster.ps1` | Delete kind cluster (requires confirmation) |
| `load-image.ps1` | Load local Docker image into kind cluster nodes |
| `port-forward.ps1` | Forward port to workload service (default: localhost:8000) |
| `smoke-endpoints.ps1` | Validate `/healthz`, `/readyz`, and `/metrics` |

### Workload Deployment (Fixed Baseline)
| Script | Purpose |
|---|---|
| `deploy-fixed.ps1` | Apply Kustomize base manifests (2 fixed replicas) |
| `inspect-fixed.ps1` | Check deployment, pods, endpoints, and events |
| `remove-fixed.ps1` | Tear down fixed-replica deployment |

### Metrics Server
| Script | Purpose |
|---|---|
| `install-metrics-server.ps1` | Deploy Metrics Server v0.7.2 with kind insecure-kubelet patch |
| `inspect-metrics-server.ps1` | Verify Metrics Server pod and resource metrics API |
| `remove-metrics-server.ps1` | Remove Metrics Server manifests |

### HPA Baseline
| Script | Purpose |
|---|---|
| `deploy-hpa.ps1` | Apply autoscaling/v2 Horizontal Pod Autoscaler overlay |
| `inspect-hpa.ps1` | Check HPA status, current/target metrics, and conditions |
| `validate-hpa-scale.ps1` | Automated test validating scale-up under load |
| `observe-hpa-scale-down.ps1` | Monitor scale-down cooling behavior |
| `remove-hpa.ps1` | Remove HPA resource (requires `-ConfirmHpaRemoval`) |

### Prometheus Monitoring
| Script | Purpose |
|---|---|
| `deploy-prometheus.ps1` | Deploy Prometheus to `kubepredict-monitoring` |
| `inspect-prometheus.ps1` | Inspect Prometheus pod, service, and scrape targets |
| `port-forward-prometheus.ps1`| Port-forward Prometheus UI/API (default: localhost:9090) |
| `validate-prometheus-scrape.ps1` | Verify scrape targets and metric availability |
| `remove-prometheus.ps1` | Remove Prometheus deployment |

### Grafana Monitoring
| Script | Purpose |
|---|---|
| `deploy-grafana.ps1` | Deploy Grafana with provisioned datasource and dashboard |
| `inspect-grafana.ps1` | Inspect Grafana pod, service, and dashboard status |
| `port-forward-grafana.ps1` | Port-forward Grafana dashboard (default: localhost:3000) |
| `remove-grafana.ps1` | Remove Grafana deployment |

---

## Load Testing Scripts (`scripts/load-testing/`)

| Script | Purpose | Example Usage |
|---|---|---|
| `validate-scenarios.ps1` | Offline validation of all scenario YAML configurations | `.\scripts\load-testing\validate-scenarios.ps1` |
| `smoke-test.ps1` | Bounded Locust load test verifying connectivity and metrics | `.\scripts\load-testing\smoke-test.ps1 -LocalPort 18000` |

---

## Experiments Scripts (`scripts/experiments/`)

| Script | Purpose | Example Usage |
|---|---|---|
| `validate-telemetry-plan.ps1` | Verify PromQL queries and alignment intervals | `.\scripts\experiments\validate-telemetry-plan.ps1` |
| `capture-scenario.ps1` | Execute scenario and capture telemetry dataset | `.\scripts\experiments\capture-scenario.ps1 -Scenario steady` |
| `capture-all-scenarios.ps1` | Sequentially run and capture all 5 test scenarios | `.\scripts\experiments\capture-all-scenarios.ps1` |
| `inspect-dataset.ps1` | Summarize and inspect captured telemetry dataset | `.\scripts\experiments\inspect-dataset.ps1 -Scenario steady` |

---

## Forecasting Scripts (`scripts/forecasting/`)

| Script | Purpose | Example Usage |
|---|---|---|
| `validate-data.ps1` | Validate raw scenario datasets for forecasting readiness | `.\scripts\forecasting\validate-data.ps1` |
| `evaluate-baselines.ps1` | Run offline evaluation across all models and horizons | `.\scripts\forecasting\evaluate-baselines.ps1` |
| `audit-selection.ps1` | Audit for temporal leakage and verify robustness | `.\scripts\forecasting\audit-selection.ps1` |
| `validate-final-model.ps1` | Validate frozen configuration against requirements | `.\scripts\forecasting\validate-final-model.ps1` |
| `inspect-results.ps1` | Inspect and summarize evaluation metrics | `.\scripts\forecasting\inspect-results.ps1` |

---

## Controller Scripts (`scripts/controller/`)

| Script | Purpose | Example Usage |
|---|---|---|
| `validate-observer.ps1` | Validate configuration and verify read-only invariants | `.\scripts\controller\validate-observer.ps1` |
| `run-observer.ps1` | Run the observe-only controller loop | `.\scripts\controller\run-observer.ps1` |
| `inspect-observer-run.ps1` | Inspect observation log output and residuals | `.\scripts\controller\inspect-observer-run.ps1` |
