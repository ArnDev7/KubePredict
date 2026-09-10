# Kubernetes Horizontal Pod Autoscaler (HPA) Baseline Specification

## 1. Overview and Purpose

The Horizontal Pod Autoscaler (HPA) baseline provides the standard reactive autoscaling mechanism against which future predictive autoscaling algorithms will be compared in the project:

> "KubePredict: Adaptive Predictive Autoscaling for Cloud-Native Applications Using Kubernetes"

In Phase 5B, this baseline configuration is designed, codified, and statically validated. The HPA manifest is **not applied to the live cluster** during this phase; live deployment and operational validation will occur in Phase 5C.

---

## 2. Prerequisites and Architectural Dependencies

The reactive HPA baseline relies strictly on standard Kubernetes resource metrics:

1. **Kubernetes Metrics Server**: Installed in `kube-system` (validated in Phase 5A).
2. **Resource Metrics APIService**: `v1beta1.metrics.k8s.io` must report `Available=True`.
3. **Container Resource Requests**: The target deployment (`kubepredict-workload-api`) must declare explicit CPU resource requests.

> [!NOTE]
> Prometheus, Prometheus Adapter, and Grafana are **not required** for standard CPU-based HPA operations, as the autoscaler queries the core `metrics.k8s.io` APIService directly. Prometheus and Grafana will be introduced in subsequent phases strictly for high-resolution observability and experimental data capture.

---

## 3. Workload Target and Resource Context

The HPA baseline targets the workload API Deployment deployed in Phase 4C:

- **Target Kind**: `Deployment`
- **Target Name**: `kubepredict-workload-api`
- **Namespace**: `kubepredict-system`
- **Container CPU Request**: `100m` (0.1 CPU cores)
- **Container CPU Limit**: `500m` (0.5 CPU cores)
- **Container Memory Request**: `128Mi`
- **Container Memory Limit**: `256Mi`

### CPU Utilization Calculation

Kubernetes HPA computes CPU utilization as a percentage of the container's configured **CPU request**, not its capacity or limit:

$$\text{Utilization} = \frac{\text{Current Pod CPU Usage}}{\text{Requested CPU (100m)}} \times 100\%$$

At the baseline target of **60% average utilization**, scaling actions trigger when the mean CPU consumption across active pods deviates from:

$$0.60 \times 100\text{m} = 60\text{m (0.06 cores)}$$

- If average CPU exceeds $60\text{m}$, the HPA calculates a higher desired replica count.
- If average CPU drops below $60\text{m}$ (subject to the downscale stabilization window), the HPA calculates a lower desired replica count down to `minReplicas`.

---

## 4. HPA Specification and Configuration

### Target API and Replicas

- **API Version**: `autoscaling/v2`
- **Kind**: `HorizontalPodAutoscaler`
- **Name**: `kubepredict-workload-api`
- **Namespace**: `kubepredict-system`
- **Minimum Replicas (`minReplicas`)**: `1`
- **Maximum Replicas (`maxReplicas`)**: `6`
- **Configured Metric**: `Resource` metric targeting `cpu` with `averageUtilization: 60`

> [!IMPORTANT]
> Scale-to-zero is **not supported** (`minReplicas: 1`). Memory metrics, custom metrics, and external metrics are explicitly excluded to maintain an isolated, defensible reactive CPU baseline.

### Scaling Behavior Policies

```yaml
behavior:
  scaleUp:
    stabilizationWindowSeconds: 0
    selectPolicy: Max
    policies:
      - type: Pods
        value: 2
        periodSeconds: 60
      - type: Percent
        value: 100
        periodSeconds: 60
  scaleDown:
    stabilizationWindowSeconds: 300
    selectPolicy: Max
    policies:
      - type: Pods
        value: 1
        periodSeconds: 60
      - type: Percent
        value: 50
        periodSeconds: 60
```

#### Scale-Up Dynamics
- **Stabilization Window**: `0s` allows immediate reaction to demand surges without artificial delay.
- **Select Policy**: `Max` selects the policy producing the greatest pod change (either +2 pods or +100% replicas within 60s), ensuring swift scale-up during traffic bursts.

#### Scale-Down Dynamics
- **Stabilization Window**: `300s` (5 minutes) provides a conservative buffer against premature scale-down, preventing flapping/thrashing when traffic temporarily dips.
- **Select Policy**: `Max` applies the larger reduction between 1 pod/60s and 50%/60s, respecting the 5-minute stabilization window.

---

## 5. Repository Structure and Manifests

The HPA resources are decoupled from `kubernetes/base` so that the fixed-replica baseline remains independently operable:

```
kubernetes/hpa/
├── hpa.yaml              # autoscaling/v2 HorizontalPodAutoscaler definition
└── kustomization.yaml    # Kustomize manifest overlay
```

### Manifest Rendering and Dry-Run

To render the HPA resource locally:
```powershell
kubectl kustomize kubernetes/hpa
```

To validate against the cluster API server without modifying state:
```powershell
kubectl apply --dry-run=server -k kubernetes/hpa --context kind-kubepredict-dev
```

---

## 6. Operational Scripts

The following PowerShell scripts manage HPA lifecycle operations:

1. **`scripts/kubernetes/deploy-hpa.ps1`**:
   - Validates prerequisites (Docker, kind, `kind-kubepredict-dev` context, node readiness, workload health, Metrics Server health, and `kubectl top`).
   - Verifies that exactly 1 HPA resource is rendered.
   - Applies `kubernetes/hpa` to the cluster and initiates bounded polling for metric resolution.
   - *(Not executed during Phase 5B; scheduled for Phase 5C).*

2. **`scripts/kubernetes/inspect-hpa.ps1`**:
   - Read-only inspection of HPA status, current vs. target metrics, scaling conditions, events, deployment status, and pod resource consumption via `kubectl top`.

3. **`scripts/kubernetes/remove-hpa.ps1`**:
   - Requires explicit `-ConfirmHpaRemoval` switch.
   - Safely deletes only HPA resources in `kubepredict-system`.
   - Leaves the workload Deployment, Service, ConfigMap, Metrics Server, and namespace intact.
   - Does not silently modify Deployment replicas; documents how to explicitly reset to 2 replicas if needed.

---

## 7. Experimental Controls and Fairness Constraints

When comparative evaluation begins in future experimental phases, the following strict controls must be observed to guarantee fair comparison between the fixed baseline, reactive HPA, and predictive autoscaler:

1. **Identical Workload**: Same container image (`kubepredict-workload-api:0.1.0`), same CPU requests (`100m`) and limits (`500m`), and identical heap/concurrency settings.
2. **Identical Infrastructure**: Same kind cluster topology (`kubepredict-dev`, single-node control-plane) running on identical host hardware and container runtime.
3. **Identical Load Scenarios**: Identical synthetic traffic traces, warm-up intervals, measurement durations, and payload sizes.
4. **Isolated Autoscale Mechanisms**:
   - During Fixed Baseline runs: No HPA and no predictive controller active (`replicas: 2`).
   - During HPA Baseline runs: Reactive HPA active; predictive controller disabled (`CONTROLLER_MODE=observe`, `ENABLE_LIVE_SCALING=false`).
   - During Predictive Controller runs: HPA removed or disabled; predictive controller actively managing replica counts.
5. **No Manual Intervention**: No out-of-band replica manipulation during measurement windows.
6. **Timeline Retention**: Raw HPA event logs, replica transition timestamps, and Prometheus scrapings archived for post-hoc analysis.

---

## 8. Current Limitations and Phase Boundaries

- **Static Validation Only**: The HPA manifest and Kustomize overlay have been created and statically verified via unit tests and server-side dry-run.
- **Not Deployed in Phase 5B**: The HPA has **not** been applied to the live cluster. The cluster remains in the fixed-replica state (2 replicas).
- **No Scaling Behavior Observed**: No scale-up, scale-down, or metric reconciliation has taken place.
- **Initial Baseline Configuration**: The 60% CPU target and behavior policies represent an initial baseline setting, not a proven optimum.
- **No Performance Claims**: No claims regarding latency, throughput, scaling responsiveness, or cost efficiency are made at this stage.
