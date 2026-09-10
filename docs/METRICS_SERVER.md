# Kubernetes Metrics Server Architecture and Validation

## 1. Overview and Purpose
Kubernetes Metrics Server is a scalable, lightweight resource metrics aggregator that collects container CPU and memory usage from Kubelets on cluster nodes via the Summary API. It exposes these metrics through the standard `metrics.k8s.io` aggregated API (`APIService v1beta1.metrics.k8s.io`).

Within KubePredict, Metrics Server provides the primary metric source required by the native Kubernetes Horizontal Pod Autoscaler (HPA) to evaluate target CPU and memory utilization thresholds in subsequent phases.

## 2. Upstream Source & Version Specification
- **Component**: Kubernetes Metrics Server
- **Selected Version**: `v0.7.2`
- **Upstream Project**: [kubernetes-sigs/metrics-server](https://github.com/kubernetes-sigs/metrics-server)
- **Official Manifest Source**: [Metrics Server v0.7.2 components.yaml](https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.7.2/components.yaml)
- **Upstream Image**: `registry.k8s.io/metrics-server/metrics-server:v0.7.2`
- **Compatibility Rationale**: Metrics Server v0.7.2 is fully compatible with Kubernetes v1.22+ up through v1.37+ (the local kind cluster runs Kubernetes v1.37.0). It adheres to non-root execution (UID 1000), read-only root filesystems, and strict RBAC boundaries.

## 3. Directory Layout and Kustomize Overlay
The Metrics Server configuration is managed under `kubernetes/metrics-server/`:
- `upstream.yaml`: Unmodified official upstream release manifest for v0.7.2.
- `patch-kind.yaml`: Strategic merge patch applying kind cluster compatibility settings.
- `kustomization.yaml`: Kustomize composition applying `patch-kind.yaml` to `upstream.yaml`.

## 4. Local kind Compatibility Patch (`patch-kind.yaml`)
In local `kind` (Kubernetes IN Docker) clusters, Kubelet serving certificates are self-signed by each container node and are not signed by the cluster root certificate authority. By default, Metrics Server validates Kubelet TLS certificates and will fail to scrape node metrics with x509 verification errors.

To enable metric scraping in the local `kind` environment, the patch adds the following container argument:
```yaml
- --kubelet-insecure-tls
```

> [!WARNING]
> The `--kubelet-insecure-tls` argument skips Kubelet certificate validation and is strictly intended for local development and testing on `kind`. It is **NOT** suitable for production Kubernetes environments, where Kubelet certificates should be properly signed by a trusted CA or CSR-approved.

## 5. Operations & Script Workflows
PowerShell helper scripts in `scripts/kubernetes/` manage the lifecycle of Metrics Server:

- **Install**:
  ```powershell
  .\scripts\kubernetes\install-metrics-server.ps1
  ```
  Applies `kubernetes/metrics-server`, waits for bounded Deployment rollout (180s), and verifies `v1beta1.metrics.k8s.io` APIService availability.

- **Inspect**:
  ```powershell
  .\scripts\kubernetes\inspect-metrics-server.ps1
  ```
  Provides read-only status of Deployment, Pods, APIService, `kubectl top nodes`, and `kubectl top pods`.

- **Remove Safeguard**:
  ```powershell
  .\scripts\kubernetes\remove-metrics-server.ps1 -ConfirmMetricsServerRemoval
  ```
  Safely deletes only resources declared in `kubernetes/metrics-server` without impacting `kube-system`, `kubepredict-system`, or the kind cluster.

## 6. Verification and Validation
- **APIService Status**: `v1beta1.metrics.k8s.io` reports `Available=True`.
- **Node Metrics**: `kubectl top nodes --context kind-kubepredict-dev` returns current CPU and memory utilization for `kubepredict-dev-control-plane`.
- **Pod Metrics**: `kubectl top pods -n kubepredict-system --context kind-kubepredict-dev --containers` returns live CPU and memory metrics for each workload replica.

## 7. Current Limitations and Scope Boundary
- **No HPA Implemented**: Horizontal Pod Autoscaler (`hpa.yaml` or `autoscaling/v2`) is **not implemented** in Phase 5A. Metrics Server serves solely as the prerequisite metric collection layer.
- **No Prometheus/Grafana**: Full cluster monitoring and long-term metric persistence remain scheduled for Phase 5B.
- **Single-Node Local Environment**: Metrics readings reflect local single-node kind execution and are not benchmark or performance data.
