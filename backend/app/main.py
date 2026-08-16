"""FastAPI application for Phase 1."""

import time
import uuid
import logging
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import IntegrityError

from app.api.routes import auth, health, projects, tasks, workers, workflows
from app.services.errors import APIError
from app.observability.logging import configure_logging, log_event, request_id_var
from app.observability.metrics import HTTP_DURATION, HTTP_REQUESTS, refresh_queue_depth
from app.observability.tracing import configure_tracing, tracer
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from app.config import get_settings

app = FastAPI(title="Distributed Task Execution & Workflow Platform", version="0.1.0")
configure_logging(get_settings().log_level)
configure_tracing()
logger = logging.getLogger("api")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(workers.router)
app.include_router(workflows.router)
app.include_router(health.router)


@app.middleware("http")
async def observe_request(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        with tracer("api").start_as_current_span("http.request") as span:
            span.set_attribute("http.request.method", request.method)
            response = await call_next(request)
            span.set_attribute("http.response.status_code", response.status_code)
    except Exception:
        log_event(logger, logging.ERROR, "request_error", "Unhandled API request error", service="api", route=request.url.path)
        request_id_var.reset(token)
        raise
    route = getattr(request.scope.get("route"), "path", request.url.path)
    elapsed = time.perf_counter() - started
    HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
    HTTP_DURATION.labels(request.method, route).observe(elapsed)
    response.headers["X-Request-ID"] = request_id
    log_event(logger, logging.INFO, "http_request", "HTTP request completed", service="api", route=route, status=response.status_code)
    request_id_var.reset(token)
    return response


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Prometheus scrape endpoint; metrics collection is in-process and non-critical."""
    refresh_queue_depth()
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)



@app.exception_handler(APIError)
async def api_error_handler(_: Request, exc: APIError) -> JSONResponse:
    error: dict[str, object] = {"code": exc.code, "message": exc.message}
    if exc.details is not None:
        error["details"] = exc.details
    return JSONResponse(status_code=exc.status_code, content={"error": error})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content=jsonable_encoder({"error": {"code": "validation_error", "message": "Request validation failed", "details": exc.errors()}}))


@app.exception_handler(IntegrityError)
async def integrity_error_handler(_: Request, __: IntegrityError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"error": {"code": "integrity_error", "message": "The request conflicts with an existing resource"}})
