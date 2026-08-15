"""FastAPI application for Phase 1."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api.routes import auth, health, projects, tasks, workers, workflows
from app.services.errors import APIError

app = FastAPI(title="Distributed Task Execution & Workflow Platform", version="0.1.0")
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(workers.router)
app.include_router(workflows.router)
app.include_router(health.router)



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
