# Docker Packaging & Local Validation

This document outlines the containerization strategy for the workload service.

## Image Architecture
The application uses a multi-stage Docker build to separate build-time dependencies from the production runtime.
- **Base Image:** `python:3.14-slim` (both builder and runtime stages).
- **Builder Stage:** Creates an isolated Python virtual environment (`/opt/venv`), installs project dependencies avoiding cache retention, and prepares the runtime environment.
- **Runtime Stage:** Runs as a dedicated non-root user (`appuser` UID `1000`), copies the virtual environment and required source files (excluding tests, caches, and development assets), and configures the `uvicorn` entrypoint.

## Excluded Build Context
The `.dockerignore` file aggressively filters the build context. The following are intentionally excluded:
- `.git` and `.gitignore`
- `.venv` and virtual environments
- Test files, results, experiment outputs, and datasets
- Caches (`__pycache__`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`)
- Development environments (`.env*`, `.vscode/`, `.idea/`)
- Security assets (`kubeconfig`, private keys, certificates)

## Health Check
The Dockerfile includes an internal health check instruction:
```dockerfile
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1
```
This avoids installing curl while ensuring the container orchestrator (or Docker Engine) can track process liveness securely via the application's built-in endpoint.

## Environment Variables
The container operates safely with defaults, but for local smoke testing, explicit variables are configured:
- `APP_HOST=0.0.0.0`
- `APP_PORT=8000`
- `LOG_LEVEL=INFO`
- `CONTROLLER_MODE=observe`
- `ENABLE_LIVE_SCALING=false`

Secrets and sensitive host variables are strictly omitted.

## Local Validation Procedure
A suite of PowerShell helper scripts under `scripts/docker/` manages the local validation workflow:
1. **Build:** `.\scripts\docker\build.ps1` builds the tagged local image (`adaptive-predictive-k8s-autoscaler:0.1.0`).
2. **Run:** `.\scripts\docker\run.ps1` starts the container in detached mode, binding to port 8000, enforcing read-only root filesystems (with `tmpfs` mounts), enforcing non-root operations (`no-new-privileges:true`), and capping memory/CPU at `512m` and `1.0`.
3. **Smoke Test:** `.\scripts\docker\smoke-test.ps1` polls until the container is marked `healthy` via Docker inspect, then hits the main endpoints including `/work` and `/metrics`.
4. **Inspect:** `.\scripts\docker\inspect.ps1` emits configuration details and container logs.

## Cleanup Procedure
Run `.\scripts\docker\stop.ps1` to stop and discard the `apa-workload-api` container. The local image is retained for the subsequent Kubernetes phase.

## Known Limitations
- The container is bound to `.venv` constraints and Python 3.14 (or 3.13 fallback) officially published Docker hub images. 
- Local resource limits (`--cpus 1.0 --memory 512m`) are enforced for desktop validation only and do not constitute production recommendations.
- Docker reachability (Windows Docker Desktop pipe) must be active for the helper scripts to succeed.

## Future Kubernetes Image-Loading
This local image (`adaptive-predictive-k8s-autoscaler:0.1.0`) is not pushed to an external registry. During Phase 4, the image must be natively side-loaded into the chosen local Kubernetes provider (e.g. `kind load docker-image` or `minikube image load`).
