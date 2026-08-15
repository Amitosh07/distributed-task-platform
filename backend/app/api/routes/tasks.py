from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
from app.db.database import get_db
from app.db.models.task import TaskPriority, TaskStatus
from app.schemas.task import TaskCreate, TaskListResponse, TaskResponse
from app.services.task_service import create_task, get_owned_task, list_tasks

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create(request: TaskCreate, response: Response, current_user: CurrentUser, db: DbSession) -> TaskResponse:
    task, was_created = create_task(db, current_user, request)
    if not was_created:
        response.status_code = status.HTTP_200_OK
    return task


@router.get("", response_model=TaskListResponse)
def list_all(
    current_user: CurrentUser, db: DbSession, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    project_id: UUID | None = None, status: TaskStatus | None = None, priority: TaskPriority | None = None,
    task_type: str | None = Query(default=None, alias="type", min_length=1, max_length=100),
) -> TaskListResponse:
    tasks, total = list_tasks(db, current_user, page=page, page_size=page_size, project_id=project_id, status=status, priority=priority, task_type=task_type)
    return TaskListResponse(items=tasks, page=page, page_size=page_size, total=total)


@router.get("/{task_id}", response_model=TaskResponse)
def get_one(task_id: UUID, current_user: CurrentUser, db: DbSession) -> TaskResponse:
    return get_owned_task(db, current_user, task_id)
