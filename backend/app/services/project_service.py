from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.project import Project
from app.db.models.user import User
from app.schemas.project import ProjectCreate
from app.services.errors import APIError


def create_project(db: Session, owner: User, request: ProjectCreate) -> Project:
    project = Project(owner_id=owner.id, name=request.name.strip())
    db.add(project)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise APIError(status_code=409, code="duplicate_project", message="You already have a project with this name") from None
    db.refresh(project)
    return project


def list_projects(db: Session, owner: User) -> list[Project]:
    return list(db.scalars(select(Project).where(Project.owner_id == owner.id).order_by(Project.created_at.desc())))


def get_owned_project(db: Session, owner: User, project_id: UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise APIError(status_code=404, code="project_not_found", message="Project not found")
    if project.owner_id != owner.id:
        raise APIError(status_code=403, code="project_access_denied", message="You do not have access to this project")
    return project
