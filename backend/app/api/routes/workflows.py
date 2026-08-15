"""API routes for Phase 5 workflows and workflow runs."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.db.models.user import User
from app.db.models.workflow import WorkflowRunNodeStatus, WorkflowRunStatus
from app.schemas.workflow import (
    WorkflowCreate,
    WorkflowEdgeResponse,
    WorkflowNodeResponse,
    WorkflowResponse,
    WorkflowListResponse,
    WorkflowRunListItemResponse,
    WorkflowRunListResponse,
    WorkflowRunNodeResponse,
    WorkflowRunResponse,
)
from app.services import workflow_engine

router = APIRouter(prefix="/v1/workflows", tags=["workflows"])
DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def _workflow_response(workflow) -> WorkflowResponse:
    node_id_to_key = {node.id: node.node_key for node in workflow.nodes}
    return WorkflowResponse(
        id=workflow.id,
        project_id=workflow.project_id,
        name=workflow.name,
        failure_policy=workflow.failure_policy.value if hasattr(workflow.failure_policy, "value") else str(workflow.failure_policy),
        nodes=[WorkflowNodeResponse(id=node.id, node_key=node.node_key, task_type=node.task_type, payload=node.payload, timeout_seconds=node.timeout_seconds, max_retries=node.max_retries) for node in workflow.nodes],
        edges=[WorkflowEdgeResponse(from_node_key=node_id_to_key.get(edge.from_node_id, str(edge.from_node_id)), to_node_key=node_id_to_key.get(edge.to_node_id, str(edge.to_node_id))) for edge in workflow.edges],
        created_at=workflow.created_at,
    )


@router.get("", response_model=WorkflowListResponse)
def list_all_workflows(
    db: DbSession, current_user: CurrentUser, project_id: UUID | None = None,
) -> WorkflowListResponse:
    """List owned workflow definitions, optionally scoped to one owned project."""
    return WorkflowListResponse(items=[_workflow_response(workflow) for workflow in workflow_engine.list_workflows(db, current_user, project_id)])


@router.post("", response_model=WorkflowResponse, status_code=201)
def create_workflow(
    request: WorkflowCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> WorkflowResponse:
    """Create and persist a new workflow DAG definition."""
    workflow = workflow_engine.create_workflow(
        db=db,
        owner=current_user,
        project_id=request.project_id,
        name=request.name,
        nodes=request.nodes,
        edges=request.edges,
        failure_policy=request.failure_policy,
    )

    return _workflow_response(workflow)


@router.get("/{workflow_id}/runs", response_model=WorkflowRunListResponse)
def list_runs(
    workflow_id: UUID, db: DbSession, current_user: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
) -> WorkflowRunListResponse:
    """List recent runs for an owned workflow."""
    runs = workflow_engine.list_workflow_runs(db, current_user, workflow_id, limit)
    return WorkflowRunListResponse(items=[
        WorkflowRunListItemResponse(
            id=run.id, workflow_id=run.workflow_id,
            status=run.status.value if hasattr(run.status, "value") else str(run.status),
            failure_policy=run.failure_policy,
            started_at=run.started_at, finished_at=run.finished_at, error_message=run.error_message,
        ) for run in runs
    ])


@router.get("/{workflow_id}", response_model=WorkflowResponse)
def get_one_workflow(workflow_id: UUID, db: DbSession, current_user: CurrentUser) -> WorkflowResponse:
    """Read one owned workflow definition for rendering its DAG."""
    return _workflow_response(workflow_engine.get_workflow(db, current_user, workflow_id))


@router.post("/{workflow_id}/run", response_model=WorkflowRunResponse, status_code=202)
def run_workflow(
    workflow_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> WorkflowRunResponse:
    """Trigger execution of a workflow run."""
    run = workflow_engine.start_workflow_run(db, current_user, workflow_id)

    nodes_response = [
        WorkflowRunNodeResponse(
            node_key=run_node.workflow_node.node_key,
            status=run_node.status.value if isinstance(run_node.status, WorkflowRunNodeStatus) else str(run_node.status),
            task_id=run_node.task_id,
            started_at=run_node.started_at,
            finished_at=run_node.finished_at,
            error_message=run_node.error_message,
        )
        for run_node in run.run_nodes
    ]

    status_str = run.status.value if isinstance(run.status, WorkflowRunStatus) else str(run.status)
    policy_str = run.failure_policy.value if hasattr(run.failure_policy, "value") else str(run.failure_policy)

    return WorkflowRunResponse(
        id=run.id,
        workflow_id=run.workflow_id,
        status=status_str,
        failure_policy=policy_str,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error_message=run.error_message,
        nodes=nodes_response,
    )


@router.get("/{workflow_id}/runs/{run_id}", response_model=WorkflowRunResponse, status_code=200)
def get_workflow_run(
    workflow_id: UUID,
    run_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> WorkflowRunResponse:
    """Inspect the status and node execution progress of a workflow run."""
    run = workflow_engine.get_workflow_run(db, current_user, workflow_id, run_id)

    nodes_response = [
        WorkflowRunNodeResponse(
            node_key=run_node.workflow_node.node_key,
            status=run_node.status.value if isinstance(run_node.status, WorkflowRunNodeStatus) else str(run_node.status),
            task_id=run_node.task_id,
            started_at=run_node.started_at,
            finished_at=run_node.finished_at,
            error_message=run_node.error_message,
        )
        for run_node in run.run_nodes
    ]

    status_str = run.status.value if isinstance(run.status, WorkflowRunStatus) else str(run.status)
    policy_str = run.failure_policy.value if hasattr(run.failure_policy, "value") else str(run.failure_policy)

    return WorkflowRunResponse(
        id=run.id,
        workflow_id=run.workflow_id,
        status=status_str,
        failure_policy=policy_str,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error_message=run.error_message,
        nodes=nodes_response,
    )
