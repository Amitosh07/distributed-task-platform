"""Import models so Alembic discovers all metadata."""

from app.db.models.project import Project
from app.db.models.task import Task, TaskPriority, TaskStatus
from app.db.models.user import User
from app.db.models.worker import Worker, WorkerStatus
from app.db.models.workflow import (
    FailurePolicy,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunNode,
    WorkflowRunNodeStatus,
    WorkflowRunStatus,
)

__all__ = [
    "FailurePolicy",
    "Project",
    "Task",
    "TaskPriority",
    "TaskStatus",
    "User",
    "Worker",
    "WorkerStatus",
    "Workflow",
    "WorkflowEdge",
    "WorkflowNode",
    "WorkflowRun",
    "WorkflowRunNode",
    "WorkflowRunNodeStatus",
    "WorkflowRunStatus",
]
