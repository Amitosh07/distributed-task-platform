from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.task import Task, TaskPriority, TaskStatus
from app.db.models.user import User
from app.schemas.task import TaskCreate
from app.services.errors import APIError
from app.services.project_service import get_owned_project

SUPPORTED_TASK_TYPES = frozenset({"sleep", "csv_stats", "image_resize", "http_check"})


def create_task(db: Session, owner: User, request: TaskCreate) -> tuple[Task, bool]:
    get_owned_project(db, owner, request.project_id)
    if request.type not in SUPPORTED_TASK_TYPES:
        raise APIError(status_code=422, code="unsupported_task_type", message="Task type is not supported")
    if request.idempotency_key:
        existing = db.scalar(select(Task).where(Task.project_id == request.project_id, Task.idempotency_key == request.idempotency_key))
        if existing is not None:
            return existing, False
    task = Task(
        project_id=request.project_id, type=request.type, payload=request.payload, priority=request.priority,
        idempotency_key=request.idempotency_key, scheduled_at=request.scheduled_at,
        timeout_seconds=request.timeout_seconds, max_retries=request.max_retries, status=TaskStatus.CREATED,
    )
    db.add(task)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if request.idempotency_key:
            existing = db.scalar(select(Task).where(Task.project_id == request.project_id, Task.idempotency_key == request.idempotency_key))
            if existing is not None:
                return existing, False
        raise APIError(status_code=409, code="duplicate_task", message="Task conflicts with an existing resource") from None
    db.refresh(task)
    return task, True


def get_owned_task(db: Session, owner: User, task_id: UUID) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise APIError(status_code=404, code="task_not_found", message="Task not found")
    get_owned_project(db, owner, task.project_id)
    return task


def list_tasks(
    db: Session, owner: User, *, page: int, page_size: int, project_id: UUID | None,
    status: TaskStatus | None, priority: TaskPriority | None, task_type: str | None,
) -> tuple[list[Task], int]:
    query = select(Task).join(Task.project).where(Task.project.has(owner_id=owner.id))
    if project_id is not None:
        get_owned_project(db, owner, project_id)
        query = query.where(Task.project_id == project_id)
    if status is not None:
        query = query.where(Task.status == status)
    if priority is not None:
        query = query.where(Task.priority == priority)
    if task_type is not None:
        query = query.where(Task.type == task_type)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    tasks = list(db.scalars(query.order_by(Task.created_at.desc()).offset((page - 1) * page_size).limit(page_size)))
    return tasks, total
