"""Worker schemas for API responses."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.db.models.worker import WorkerStatus


class WorkerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    hostname: str
    status: WorkerStatus
    started_at: datetime
    last_heartbeat_at: datetime
    stopped_at: datetime | None = None


class WorkerListResponse(BaseModel):
    items: list[WorkerResponse]
    total: int
