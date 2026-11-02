# DEMO Video Script

**Total runtime: ~9 minutes**

| Time | Screen / View | Command | Talking points |
|------|----------------|---------|----------------|
| 00:00–00:30 | Terminal – repository root | `git status` (optional) | Briefly introduce the repository layout (folders `app/`, `controller/`, `kubernetes/`, `experiments/`, `scripts/`). Mention that all tests have passed (491 tests). |
| 00:30–01:00 | Terminal – `kubectl config current-context` | `kubectl config current-context` | Verify we are on **kind‑kubepredict‑dev**. Explain why the correct context is crucial. |
| 01:00–01:30 | Terminal – cluster health | `kubectl get nodes && kubectl get pods -A` | Show that all nodes are `Ready` and the workload/controller pods are up (controller in `dry_run`). |
| 01:30–02:00 | Terminal – start demo | `./scripts/demo/start-demo.ps1` | Explain that this script validates the cluster, starts port‑forwards for API (8000), Prometheus (9090), Grafana (3000) and controller metrics (9101). Mention the state file `.demo-state/state.json`. |
| 02:00–02:20 | Terminal – state file preview | `cat .demo-state/state.json` (PowerShell: `Get-Content .demo-state\state.json | ConvertFrom-Json`) | Show the actual ports and PIDs; note that if a preferred port was busy an alternative was chosen. |
| 02:20–03:00 | Terminal – generate traffic | `./scripts/demo/load-demo.ps1` | Walk through the four GET requests (`/`, `/healthz`, `/readyz`) and the five POST `/work` calls with safe payload. Mention the optional 30 s Locust run (if present). |
| 03:00–03:20 | Grafana UI (browser) | Open `http://localhost:3000` → select **KubePredict Overview** dashboard | Explain that Grafana is already running; set **time range** to **Last 5 minutes**, **refresh** to **5 s**. Highlight panels: **CPU usage**, **Requests/sec**, **Replica count**. |
| 03:20–03:45 | Grafana – observe telemetry | (no command) | While the script is running you should see the metrics appear and stabilize. Point out the **Prometheus query** panel that shows `up{job="kubepredict-workload"}` becoming 2. |
| 03:45–04:00 | Terminal – stop demo | `./scripts/demo/stop-demo.ps1` | Explain that this kills the port‑forwards and cleans the `.demo-state` directory. |
| 04:00–04:30 | Terminal – final verification | `kubectl get configmap kubepredict-controller-config -n kubepredict-system -o yaml` | Show that `CONTROLLER_MODE=dry_run` and `ENABLE_LIVE_SCALING=false` are still set. |
| 04:30–04:50 | Terminal – workload replica check | `kubectl get deployment kubepredict-workload-api -n kubepredict-system -o jsonpath="{.status.availableReplicas}"` | Confirm exactly **2** replicas are ready. |
| 04:50–05:00 | Terminal – HPA check | `kubectl get hpa -A --ignore-not-found` | Verify that no live HPA exists. |
| 05:00–05:30 | (Optional) Live‑scaling demo intro | – | State that the next section is **optional** and requires explicit confirmation. |
| 05:30–06:00 | Terminal – run rehearsal with live scaling | `./scripts/demo/rehearse-demo.ps1 -ConfirmLiveScalingDemo` | Describe what the script does: enables live mode, runs a 90 s Locust load (4 users, max 100k iterations), watches for the first replica increase, then disables live mode and restores baseline. |
| 06:00–06:20 | Terminal – monitor scaling event | `kubectl get deployment kubepredict-workload-api -n kubepredict-system -w` (watch) | Show the replica count jumping from 2 to 3 (or higher) once. Explain that the script will stop after the first successful scale‑up. |
| 06:20–06:40 | Grafana – live scaling view | Same Grafana URL, switch time range to **Last 10 minutes** | Observe the replica count panel reflecting the temporary scale‑up. |
| 06:40–07:00 | Terminal – script cleanup | (script automatically disables live scaling) | Explain that the script now runs `disable-live-scaling.ps1`, reapplies base manifests, and ensures the workload returns to 2 replicas. |
| 07:00–07:20 | Terminal – final state check (again) | Same commands as 04:30‑05:00 | Re‑confirm the cluster is back to the safe baseline. |
| 07:20–07:40 | Evidence directory listing | `ls experiments/output/demo-rehearsal/<timestamp>` | Show `controller.log` and `metadata.json`. Point out the `live_scaling_success` flag. |
| 07:40–08:00 | Closing remarks | – | Summarize what was demonstrated: repository health, dry‑run controller decisions, optional safe live scaling, and clean restoration. Mention that all artifacts are stored under `experiments/output/demo-rehearsal/` and are ignored by Git. |

**Notes for the presenter**
- Keep each segment concise; aim for ~30 seconds per block.
- If you choose **not** to run the live‑scaling part, simply skip the optional segment and stop at 5 minutes.
- When showing Grafana, switch to *Full screen* mode for better video quality.
- Ensure the terminal window has a dark background for readability.

---
End of script.

