import time

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.exceptions import SyntheticWorkFailure
from app.schemas import WorkRequest, WorkResponse
from app.workload import perform_cpu_work, perform_delay, perform_payload_work

router = APIRouter()

# Global readiness state for tests
is_ready = True


@router.get("/")
def get_root():
    return {
        "service": "adaptive-predictive-k8s-autoscaler",
        "version": "0.1.0",
        "status": "running",
        "docs": "/docs",
    }


@router.get("/healthz")
def get_health():
    return {"status": "healthy"}


@router.get("/readyz")
def get_ready():
    if is_ready:
        return {"status": "ready"}
    return JSONResponse(status_code=503, content={"status": "not_ready"})


@router.get("/metrics")
def get_metrics(request: Request):
    registry = request.app.state.metrics_registry
    return Response(generate_latest(registry), headers={"Content-Type": CONTENT_TYPE_LATEST})


@router.post("/work", response_model=WorkResponse)
async def post_work(work: WorkRequest, request: Request):
    settings = request.app.state.settings
    metrics = request.app.state.metrics
    request_id = request.state.request_id

    # validate runtime bounds
    if work.cpu_iterations > settings.max_cpu_iterations:
        metrics.work_requests_total.labels(outcome="rejected").inc()
        raise HTTPException(status_code=422, detail="cpu_iterations exceeds maximum allowed")

    if work.delay_ms > settings.max_delay_ms:
        metrics.work_requests_total.labels(outcome="rejected").inc()
        raise HTTPException(status_code=422, detail="delay_ms exceeds maximum allowed")

    if work.fail:
        metrics.work_requests_total.labels(outcome="controlled_failure").inc()
        raise SyntheticWorkFailure(request_id=request_id)

    start_time = time.perf_counter()

    checksum = perform_cpu_work(work.cpu_iterations)
    await perform_delay(work.delay_ms)
    perform_payload_work(work.payload_size)

    duration_ms = (time.perf_counter() - start_time) * 1000.0

    metrics.work_requests_total.labels(outcome="completed").inc()
    metrics.cpu_iterations_total.inc(work.cpu_iterations)
    metrics.artificial_delay_seconds_total.inc(work.delay_ms / 1000.0)
    metrics.work_duration_seconds.observe(duration_ms / 1000.0)

    return WorkResponse(
        request_id=request_id,
        status="completed",
        cpu_iterations_completed=work.cpu_iterations,
        delay_ms_applied=work.delay_ms,
        payload_size=work.payload_size,
        checksum=checksum,
        duration_ms=duration_ms,
    )
