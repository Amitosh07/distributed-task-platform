from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.queue.redis_client import check_redis_health
from app.services.errors import APIError

router = APIRouter(prefix="/health", tags=["health"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/live")
def live() -> dict[str, str]:
    """Liveness: API process is alive. Independent of Redis or PostgreSQL."""
    return {"status": "ok"}


@router.get("/ready")
def ready(db: DbSession) -> dict[str, str]:
    """Readiness: PostgreSQL AND Redis must be reachable."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise APIError(status_code=503, code="database_unavailable", message="Database is not ready") from None

    if not check_redis_health():
        raise APIError(status_code=503, code="redis_unavailable", message="Redis is not ready")

    return {"status": "ready"}
