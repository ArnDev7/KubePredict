# Fixed-Replica Workload Baseline Specification

## 1. Overview and Purpose
The fixed-replica baseline serves as the experimental control configuration for KubePredict research. It provides a stable, unscaled reference benchmark against which default Kubernetes Horizontal Pod Autoscaler (HPA) and predictive autoscaling policies will be evaluated.

## 2. Resource Specifications
- **Cluster**: `kubepredict-dev`
- **Context**: `kind-kubepredict-dev`
- **Namespace**: `kubepredict-system`
- **Deployment Name**: `kubepredict-workload-api`
- **Service Name**: `kubepredict-workload-api` (ClusterIP, Port 8000 -> named port `http`)
- **Image**: `kubepredict-workload-api:0.1.0` (`imagePullPolicy: IfNotPresent`)
- **Fixed Replicas**: `2`
- **Rollout Strategy**: `RollingUpdate` (`maxUnavailable: 0`, `maxSurge: 1`)

## 3. Resource Allocations
- **Requests**:
  - CPU: `100m`
  - Memory: `128Mi`
- **Limits**:
  - CPU: `500m`
  - Memory: `256Mi`

*Note: These resource values represent local experimental baseline settings and are not intended as production cloud recommendations.*

## 4. Health and Readiness Probes
- **Startup Probe**: HTTP GET `/healthz` on port `http` (`periodSeconds: 2`, `timeoutSeconds: 1`, `failureThreshold: 15`).
- **Liveness Probe**: HTTP GET `/healthz` on port `http` (`initialDelaySeconds: 10`, `periodSeconds: 10`, `timeoutSeconds: 2`, `failureThreshold: 3`, `successThreshold: 1`).
- **Readiness Probe**: HTTP GET `/readyz` on port `http` (`initialDelaySeconds: 3`, `periodSeconds: 5`, `timeoutSeconds: 2`, `failureThreshold: 3`, `successThreshold: 1`).

## 5. Security & Isolation Controls
- **Pod Security Context**:
  - `runAsNonRoot: true`
  - `runAsUser: 1000`
  - `runAsGroup: 1000`
  - `seccompProfile.type: RuntimeDefault`
- **Container Security Context**:
  - `allowPrivilegeEscalation: false`
  - `readOnlyRootFilesystem: true`
  - `runAsNonRoot: true`
  - `capabilities.drop: ["ALL"]`
- **Filesystem**: Read-only root filesystem with a dedicated `emptyDir` volume for temporary files (`sizeLimit: 64Mi`) mounted at `/tmp`.
- **API Access**: `automountServiceAccountToken: false` (the workload service requires zero Kubernetes API credentials).
- **Termination Grace Period**: `15` seconds.

## 6. Configuration Management
All non-secret environment variables are externalized into `kubepredict-workload-api-config` (`ConfigMap` in `kubernetes/base/configmap.yaml`) and loaded via `envFrom`. Key defaults:
- `CONTROLLER_MODE: "observe"`
- `ENABLE_LIVE_SCALING: "false"`
- `MINIMUM_REPLICAS: "1"`
- `MAXIMUM_REPLICAS: "10"`
- `COOLDOWN_SECONDS: "60"`

## 7. Kustomize Structure
The baseline configuration is managed declaratively under `kubernetes/base/`:
- `namespace.yaml`: Namespace declaration for `kubepredict-system`.
- `configmap.yaml`: Application and controller settings.
- `deployment.yaml`: 2-replica Deployment with probes, limits, and security constraints.
- `service.yaml`: ClusterIP Service exposing port 8000.
- `kustomization.yaml`: Base composition manifest.

## 8. Deployment and Removal Procedures
- **Deploy**: `.\scripts\kubernetes\deploy-fixed.ps1`
- **Inspect**: `.\scripts\kubernetes\inspect-fixed.ps1`
- **Port Forward**: `.\scripts\kubernetes\port-forward.ps1`
- **Remove**: `.\scripts\kubernetes\remove-fixed.ps1 -ConfirmFixedBaselineRemoval`

## 9. Runtime Validation Findings (Phase 4C)
- **Validation Date**: 2026-09-03 / 2026-09-04
- **Cluster Name**: `kubepredict-dev`
- **Kubernetes Context**: `kind-kubepredict-dev`
- **Cluster Topology**: 1 control-plane node (`kubepredict-dev-control-plane`), status `Ready`
- **Namespace**: `kubepredict-system`
- **Image Reference**: `kubepredict-workload-api:0.1.0` (`imagePullPolicy: IfNotPresent`, present on node containerd runtime)
- **Deployment**: `kubepredict-workload-api`
  - Desired Replicas: `2`
  - Updated Replicas: `2`
  - Available Replicas: `2`
  - Ready Replicas: `2`
- **Pod Status**:
  - `kubepredict-workload-api-7649cd9b6-czpjm`: Status `Running`, Ready `1/1`, Restarts `0`, Node `kubepredict-dev-control-plane`, QoS `Burstable`
  - `kubepredict-workload-api-7649cd9b6-fnlpj`: Status `Running`, Ready `1/1`, Restarts `0`, Node `kubepredict-dev-control-plane`, QoS `Burstable`
- **Service Status**: `kubepredict-workload-api`
  - Type: `ClusterIP`
  - Port: `8000/TCP` -> TargetPort `http` (`8000/TCP`)
  - Ready Backend Addresses: `2` (`10.244.0.5:8000`, `10.244.0.6:8000`)
  - External IP / NodePort / LoadBalancer: None
- **Resource Allocations per Container**:
  - Requests: CPU `100m`, Memory `128Mi`
  - Limits: CPU `500m`, Memory `256Mi`
  - Quality of Service (QoS): `Burstable`
- **Probe Status**:
  - Startup Probe: HTTP GET `/healthz` on port `http` (`periodSeconds: 2`, `failureThreshold: 15`) - Passed
  - Liveness Probe: HTTP GET `/healthz` on port `http` - Passed
  - Readiness Probe: HTTP GET `/readyz` on port `http` - Passed
- **Kubernetes Security Configuration**:
  - Pod: `runAsNonRoot: true`, `runAsUser: 1000`, `runAsGroup: 1000`, `seccompProfile: RuntimeDefault`, `automountServiceAccountToken: false`
  - Host isolation: `hostNetwork`, `hostPID`, `hostIPC` all absent/false
  - Container: `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities.drop: ["ALL"]`, `privileged: false`
  - Storage: `/tmp` mounted from `emptyDir` (sizeLimit `64Mi`), no `hostPath`, no host socket, no Secrets mounted
  - API Access: No mounted ServiceAccount token; workload requires no Kubernetes RBAC permissions
- **Endpoint Smoke-Test Results**:
  - `GET /`: HTTP `200 OK`, returned `{"service":"adaptive-predictive-k8s-autoscaler","version":"0.1.0","status":"running","docs":"/docs"}`
  - `GET /healthz`: HTTP `200 OK`, returned `{"status":"healthy"}`
  - `GET /readyz`: HTTP `200 OK`, returned `{"status":"ready"}`
  - `POST /work`: HTTP `200 OK`, executed CPU iterations and applied artificial delay; returned valid checksum and non-negative `duration_ms`
  - Request-ID Round Trip: Sent `X-Request-ID: phase4c-fixed-baseline`, response returned matching header and response body `request_id`
  - Invalid-Bound Handling: Request with `cpu_iterations: 600000` (exceeding `MAX_CPU_ITERATIONS=500000`) returned HTTP `422 Unprocessable Content` (`{"detail":"cpu_iterations exceeds maximum allowed"}`); pods remained Ready with 0 restarts
  - `GET /metrics`: HTTP `200 OK`, valid Prometheus exposition format containing `apa_` metrics without raw URLs or request IDs as labels
- **Individual Pod Validation**:
  - Pod `czpjm` individually port-forwarded: verified HTTP `200` on `/healthz` and `/work`; zero restarts
  - Pod `fnlpj` individually port-forwarded: verified HTTP `200` on `/healthz` and `/work`; zero restarts
- **Port-Forward Cleanup**:
  - All temporary Service and pod port-forward processes (PIDs 31732, 31844, 23856) terminated cleanly
  - Local ports `8000`, `18001`, and `18002` confirmed released

## 10. Explicit Baseline Constraints and Limitations
- This was a deployment and functional smoke test.
- This was not a comparative performance experiment.
- No HPA comparison has been performed.
- No predictive controller comparison has been performed.
- No latency or resource improvement is claimed.
- Metrics Server, Prometheus server, and Grafana are not yet deployed in this phase.

