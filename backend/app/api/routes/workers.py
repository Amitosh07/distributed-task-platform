"""Worker status and health routes."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.db.models.user import User
from app.db.models.worker import Worker
from app.schemas.worker import WorkerListResponse, WorkerResponse


router = APIRouter(prefix="/v1/workers", tags=["workers"])
DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("", response_model=WorkerListResponse)
def list_workers(db: DbSession, _user: CurrentUser) -> WorkerListResponse:
    """List all registered workers and their current heartbeat status."""
    workers = list(db.scalars(select(Worker).order_by(Worker.started_at.desc())).all())
    return WorkerListResponse(items=workers, total=len(workers))
