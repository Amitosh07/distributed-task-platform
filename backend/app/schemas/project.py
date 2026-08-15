from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r".*\S.*")


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    owner_id: UUID
    name: str
    status: str
    created_at: datetime


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
