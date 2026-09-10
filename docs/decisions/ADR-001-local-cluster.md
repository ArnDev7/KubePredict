# Local Cluster Provider Decision

Status: Accepted

## Context
During Phase 0, the environment was verified as a Windows system with Docker installed, but the daemon was temporarily unreachable and no Kubernetes cluster was configured. In Phase 3 and Phase 4A, Docker Desktop was verified as running and reachable (`docker version 29.6.1`, Docker Desktop `4.82.0`). The project requires a reproducible local Kubernetes environment capable of hosting the containerized workload application, Prometheus monitoring, and the predictive autoscaler controller.

## Decision
`kind` (Kubernetes IN Docker) is accepted as the local Kubernetes cluster provider for KubePredict development and testing.

- **kind Version**: `v0.33.0` (installed via winget package `Kubernetes.kind`).
- **Cluster Name**: `kubepredict-dev`
- **Context Name**: `kind-kubepredict-dev`
- **Selected Topology**: Single control-plane node (`control-plane`), selected based on transient available host memory (< 6 GB available at configuration time).
- **Rationale**: `kind` runs directly on the verified Docker Desktop engine, supports declarative multi-node configurations, and allows local image side-loading (`kind load docker-image`) without requiring an external registry or remote network transfer.
- **Evaluation Scope**: Minikube was not evaluated and is not claimed to be inferior; `kind` was chosen for direct compatibility with the verified Docker Desktop environment.
- **Memory Note**: Available memory influenced only the node topology selection (single control-plane node vs control-plane plus worker) and does not permanently categorize host capabilities.
- **Current State**: The cluster infrastructure and image pre-loading are verified. Application manifests, namespaces, controllers, and workloads have not yet been deployed.
