from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
from app.db.database import get_db
from app.schemas.project import ProjectCreate, ProjectListResponse, ProjectResponse
from app.services.project_service import create_project, get_owned_project, list_projects

router = APIRouter(prefix="/v1/projects", tags=["projects"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create(request: ProjectCreate, current_user: CurrentUser, db: DbSession) -> ProjectResponse:
    return create_project(db, current_user, request)


@router.get("", response_model=ProjectListResponse)
def list_all(current_user: CurrentUser, db: DbSession) -> ProjectListResponse:
    return ProjectListResponse(items=list_projects(db, current_user))


@router.get("/{project_id}", response_model=ProjectResponse)
def get_one(project_id: UUID, current_user: CurrentUser, db: DbSession) -> ProjectResponse:
    return get_owned_project(db, current_user, project_id)
