from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models.task import TaskPriority, TaskStatus


class TaskCreate(BaseModel):
    project_id: UUID
    type: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: TaskPriority = TaskPriority.NORMAL
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)
    scheduled_at: datetime | None = None
    timeout_seconds: int = Field(default=300, ge=1, le=86_400)
    max_retries: int = Field(default=3, ge=0, le=100)

    @field_validator("scheduled_at")
    @classmethod
    def scheduled_at_is_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("scheduled_at must include a timezone")
        return value


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID
    type: str
    payload: dict[str, Any]
    status: TaskStatus
    priority: TaskPriority
    idempotency_key: str | None
    scheduled_at: datetime | None
    timeout_seconds: int
    max_retries: int
    attempt_count: int
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    created_at: datetime
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    result_summary: dict[str, Any] | None
    error_message: str | None



class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    page: int
    page_size: int
    total: int
