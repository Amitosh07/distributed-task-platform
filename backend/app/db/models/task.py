from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum as SqlEnum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.database import Base


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    RETRY_WAIT = "RETRY_WAIT"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class TaskPriority(str, Enum):
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


VALID_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELLED}),
    TaskStatus.QUEUED: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset({TaskStatus.SUCCESS, TaskStatus.RETRY_WAIT, TaskStatus.FAILED, TaskStatus.DEAD_LETTER, TaskStatus.TIMED_OUT}),
    TaskStatus.RETRY_WAIT: frozenset({TaskStatus.QUEUED}),
    TaskStatus.SUCCESS: frozenset(), TaskStatus.FAILED: frozenset(), TaskStatus.DEAD_LETTER: frozenset(),
    TaskStatus.CANCELLED: frozenset(), TaskStatus.TIMED_OUT: frozenset(),
}


def is_valid_transition(current: TaskStatus, target: TaskStatus) -> bool:
    return target in VALID_TRANSITIONS[current]


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_tasks_project_idempotency_key"),
        Index("ix_tasks_project_status", "project_id", "status"),
        Index("ix_tasks_status_priority_created", "status", "priority", "created_at"),
        Index("ix_tasks_scheduled_at", "scheduled_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[TaskStatus] = mapped_column(SqlEnum(TaskStatus, name="task_status"), nullable=False, default=TaskStatus.CREATED)
    priority: Mapped[TaskPriority] = mapped_column(SqlEnum(TaskPriority, name="task_priority"), nullable=False, default=TaskPriority.NORMAL)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="tasks")
