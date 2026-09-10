import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CollectorRegistry

from app.exceptions import SyntheticWorkFailure
from app.logging_config import setup_logging
from app.metrics import AppMetrics
from app.routes import router
from config.settings import Settings, get_settings


def create_app(
    settings: Settings | None = None, metrics_registry: CollectorRegistry | None = None
) -> FastAPI:
    app_settings = settings or get_settings()
    registry = metrics_registry or CollectorRegistry()

    setup_logging(app_settings.log_level)
    logger = logging.getLogger(__name__)

    app = FastAPI(
        title="adaptive-predictive-k8s-autoscaler",
        description="Controlled workload service for autoscaling research",
        version="0.1.0",
    )

    app.state.settings = app_settings
    app.state.metrics_registry = registry
    app.state.metrics = AppMetrics(registry)
    app.state.metrics.application_info.info({"version": "0.1.0"})

    @app.middleware("http")
    async def request_id_and_metrics_middleware(request: Request, call_next):
        req_id = request.headers.get("X-Request-ID", "")
        if not req_id or len(req_id) > 128 or not all(c.isalnum() or c in "-_." for c in req_id):
            req_id = str(uuid.uuid4())

        request.state.request_id = req_id

        route = request.url.path
        allowed_routes = ["/", "/healthz", "/readyz", "/metrics", "/work"]
        if route not in allowed_routes:
            route = "unknown"

        method = request.method

        app.state.metrics.http_requests_in_progress.labels(method=method, route=route).inc()

        start_time = time.perf_counter()

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            status_code = 500
            raise
        finally:
            duration = time.perf_counter() - start_time
            app.state.metrics.http_requests_in_progress.labels(method=method, route=route).dec()
            app.state.metrics.http_requests_total.labels(
                method=method, route=route, status_code=status_code
            ).inc()
            app.state.metrics.http_request_duration_seconds.labels(
                method=method, route=route
            ).observe(duration)

            logger.info(
                f"method={method} route={route} status={status_code} duration_ms={duration * 1000:.2f} request_id={req_id}"
            )

        response.headers["X-Request-ID"] = req_id
        return response

    @app.exception_handler(SyntheticWorkFailure)
    async def synthetic_work_failure_handler(request: Request, exc: SyntheticWorkFailure):
        return JSONResponse(
            status_code=503,
            content={
                "error": "synthetic_failure",
                "message": "Controlled failure requested",
                "request_id": exc.request_id,
            },
            headers={"X-Request-ID": exc.request_id},
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        req_id = getattr(request.state, "request_id", "unknown")
        logger.error(f"Unexpected error request_id={req_id}", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "An unexpected error occurred",
                "request_id": req_id,
            },
            headers={"X-Request-ID": req_id},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        if request.url.path == "/work":
            app.state.metrics.work_requests_total.labels(outcome="rejected").inc()

        req_id = getattr(request.state, "request_id", "unknown")
        return JSONResponse(
            status_code=422,
            content={"error": "validation_error", "detail": exc.errors(), "request_id": req_id},
            headers={"X-Request-ID": req_id},
        )

    app.include_router(router)
    return app
