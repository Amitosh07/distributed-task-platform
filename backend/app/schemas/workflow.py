from typing import Any
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

class WorkflowNodeCreate(BaseModel):
    node_key: str = Field(min_length=1, max_length=120)
    task_type: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=300, ge=1, le=86400)
    max_retries: int = Field(default=3, ge=0, le=100)

class WorkflowEdgeCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_node: str = Field(alias="from", min_length=1, max_length=120)
    to_node: str = Field(alias="to", min_length=1, max_length=120)

class WorkflowCreate(BaseModel):
    project_id: UUID
    name: str = Field(min_length=1, max_length=120)
    nodes: list[WorkflowNodeCreate] = Field(min_length=1)
    edges: list[WorkflowEdgeCreate] = Field(default_factory=list)
    failure_policy: str = Field(default="FAIL_FAST", pattern=r"^(FAIL_FAST|CONTINUE)$")

class WorkflowNodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    node_key: str
    task_type: str
    payload: dict[str, Any]
    timeout_seconds: int
    max_retries: int

class WorkflowEdgeResponse(BaseModel):
    from_node_key: str
    to_node_key: str

class WorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID
    name: str
    failure_policy: str
    nodes: list[WorkflowNodeResponse]
    edges: list[WorkflowEdgeResponse]
    created_at: datetime

class WorkflowRunNodeResponse(BaseModel):
    node_key: str
    status: str
    task_id: UUID | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None

class WorkflowRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workflow_id: UUID
    status: str
    failure_policy: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None
    nodes: list[WorkflowRunNodeResponse] = []
