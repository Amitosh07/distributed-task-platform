"""Workflow DAG orchestration engine service.

Handles:
- DAG structural and cycle validation (Kahn's algorithm)
- Workflow definition persistence (workflows, nodes, edges)
- Workflow run instantiation and root node dispatch
- Post-task completion advancement and dependency evaluation
- Atomic node dispatch preventing duplicate task enqueue
- Failure policies (FAIL_FAST, CONTINUE) and cascading skip propagation
- Workflow completion detection

Architecture:
    Workflow API -> Workflow Engine -> Task records in PostgreSQL -> Redis Queue -> Workers -> Task Handlers
"""

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, joinedload

from app.db.models.task import Task, TaskStatus
from app.db.models.user import User
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
from app.queue.publisher import publish_task
from app.services.errors import APIError
from app.services.project_service import get_owned_project
from app.services.task_service import SUPPORTED_TASK_TYPES

logger = logging.getLogger("workflow.engine")


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# DAG Validation
# ---------------------------------------------------------------------------

def validate_workflow(nodes: list[Any], edges: list[Any]) -> None:
    """Validate workflow DAG structure, node payloads, and acyclicity."""
    errors: list[str] = []

    if not nodes:
        errors.append("Workflow must contain at least one node.")
        raise APIError(
            status_code=422,
            code="validation_error",
            message="Workflow validation failed",
            details={"errors": errors},
        )

    node_keys: set[str] = set()
    for idx, node in enumerate(nodes):
        key = getattr(node, "node_key", None)
        if key is None and isinstance(node, dict):
            key = node.get("node_key") or node.get("key")

        if not key or not str(key).strip():
            errors.append(f"Node at index {idx} has an empty or missing node_key.")
            continue

        key = str(key).strip()
        if key in node_keys:
            errors.append(f"Duplicate node key '{key}'.")
        node_keys.add(key)

        task_type = getattr(node, "task_type", None)
        if task_type is None and isinstance(node, dict):
            task_type = node.get("task_type")

        if not task_type or task_type not in SUPPORTED_TASK_TYPES:
            errors.append(
                f"Node '{key}' has unsupported task type '{task_type}'. Supported types: {sorted(SUPPORTED_TASK_TYPES)}."
            )
        else:
            payload = getattr(node, "payload", None)
            if payload is None and isinstance(node, dict):
                payload = node.get("payload", {})
            if not isinstance(payload, dict):
                errors.append(f"Node '{key}' payload must be a JSON object.")
            else:
                # Structural required field validation
                if task_type == "sleep" and "seconds" not in payload:
                    errors.append(f"Node '{key}' (sleep) requires 'seconds' in payload.")
                elif task_type == "csv_stats" and "csv_data" not in payload:
                    errors.append(f"Node '{key}' (csv_stats) requires 'csv_data' in payload.")
                elif task_type == "image_resize":
                    for req_f in ("image_b64", "width", "height"):
                        if req_f not in payload:
                            errors.append(f"Node '{key}' (image_resize) requires '{req_f}' in payload.")
                elif task_type == "http_check" and "url" not in payload:
                    errors.append(f"Node '{key}' (http_check) requires 'url' in payload.")

    edge_set: set[tuple[str, str]] = set()
    in_degree: dict[str, int] = {k: 0 for k in node_keys}
    graph: dict[str, list[str]] = {k: [] for k in node_keys}

    for idx, edge in enumerate(edges):
        from_node = getattr(edge, "from_node", None)
        if from_node is None and isinstance(edge, dict):
            from_node = edge.get("from_node") or edge.get("from") or edge.get("from_node_key")

        to_node = getattr(edge, "to_node", None)
        if to_node is None and isinstance(edge, dict):
            to_node = edge.get("to_node") or edge.get("to") or edge.get("to_node_key")

        if not from_node or not to_node:
            errors.append(f"Edge at index {idx} must specify both 'from' and 'to' nodes.")
            continue

        from_node = str(from_node).strip()
        to_node = str(to_node).strip()

        if from_node not in node_keys:
            errors.append(f"Edge references unknown source node '{from_node}'.")
        if to_node not in node_keys:
            errors.append(f"Edge references unknown target node '{to_node}'.")

        if from_node == to_node:
            errors.append(f"Self-loop detected on node '{from_node}'.")

        edge_tuple = (from_node, to_node)
        if edge_tuple in edge_set:
            errors.append(f"Duplicate edge from '{from_node}' to '{to_node}'.")
        else:
            edge_set.add(edge_tuple)
            if from_node in graph and to_node in in_degree:
                graph[from_node].append(to_node)
                in_degree[to_node] += 1

    if not errors and node_keys:
        # Kahn's algorithm for cycle detection
        queue = [k for k, deg in in_degree.items() if deg == 0]
        visited_count = 0

        while queue:
            curr = queue.pop(0)
            visited_count += 1
            for nxt in graph.get(curr, []):
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if visited_count != len(node_keys):
            errors.append("Cycle detected: workflow graph must be a directed acyclic graph (DAG).")

    if errors:
        raise APIError(
            status_code=422,
            code="validation_error",
            message="Workflow validation failed",
            details={"errors": errors},
        )


# ---------------------------------------------------------------------------
# Workflow CRUD
# ---------------------------------------------------------------------------

def create_workflow(
    db: Session,
    owner: User,
    project_id: UUID,
    name: str,
    nodes: list[Any],
    edges: list[Any],
    failure_policy: str = "FAIL_FAST",
) -> Workflow:
    """Create and persist a validated workflow definition."""
    get_owned_project(db, owner, project_id)

    policy_str = failure_policy.upper() if isinstance(failure_policy, str) else failure_policy.value
    if policy_str not in ("FAIL_FAST", "CONTINUE"):
        raise APIError(
            status_code=422,
            code="validation_error",
            message=f"Invalid failure_policy '{failure_policy}'. Must be 'FAIL_FAST' or 'CONTINUE'.",
        )

    validate_workflow(nodes, edges)

    workflow = Workflow(
        project_id=project_id,
        name=name.strip(),
        failure_policy=policy_str,
    )
    db.add(workflow)
    db.flush()

    node_key_to_id: dict[str, UUID] = {}
    for n in nodes:
        node_key = getattr(n, "node_key", None) or (n.get("node_key") or n.get("key") if isinstance(n, dict) else None)
        task_type = getattr(n, "task_type", None) or (n.get("task_type") if isinstance(n, dict) else None)
        payload = getattr(n, "payload", None) if not isinstance(n, dict) else n.get("payload", {})
        timeout_seconds = getattr(n, "timeout_seconds", 300) if not isinstance(n, dict) else n.get("timeout_seconds", 300)
        max_retries = getattr(n, "max_retries", 3) if not isinstance(n, dict) else n.get("max_retries", 3)

        node = WorkflowNode(
            workflow_id=workflow.id,
            node_key=str(node_key).strip(),
            task_type=str(task_type).strip(),
            payload=payload or {},
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        db.add(node)
        db.flush()
        node_key_to_id[node.node_key] = node.id

    for e in edges:
        from_node = getattr(e, "from_node", None) or (e.get("from_node") or e.get("from") or e.get("from_node_key") if isinstance(e, dict) else None)
        to_node = getattr(e, "to_node", None) or (e.get("to_node") or e.get("to") or e.get("to_node_key") if isinstance(e, dict) else None)

        edge = WorkflowEdge(
            workflow_id=workflow.id,
            from_node_id=node_key_to_id[str(from_node).strip()],
            to_node_id=node_key_to_id[str(to_node).strip()],
        )
        db.add(edge)

    db.commit()

    # Eagerly load full structure
    created_workflow = db.scalar(
        select(Workflow)
        .options(joinedload(Workflow.nodes), joinedload(Workflow.edges))
        .filter_by(id=workflow.id)
    )
    logger.info("workflow_created workflow_id=%s name=%s nodes=%d edges=%d", workflow.id, name, len(nodes), len(edges))
    return created_workflow


# ---------------------------------------------------------------------------
# Workflow Run Execution & Node Dispatch
# ---------------------------------------------------------------------------

def _dispatch_node(db: Session, run_node: WorkflowRunNode, workflow: Workflow) -> bool:
    """Atomically transition a run node from PENDING to RUNNING and enqueue its task.

    Returns True if successfully claimed and dispatched; False if already dispatched.
    """
    now = _now_utc()
    stmt = (
        update(WorkflowRunNode)
        .where(
            WorkflowRunNode.id == run_node.id,
            WorkflowRunNode.status == WorkflowRunNodeStatus.PENDING,
        )
        .values(status=WorkflowRunNodeStatus.RUNNING, started_at=now)
    )
    result = db.execute(stmt)
    if result.rowcount == 0:
        return False

    workflow_node = db.get(WorkflowNode, run_node.workflow_node_id)
    if workflow_node is None:
        logger.error("WorkflowNode %s not found for run_node %s", run_node.workflow_node_id, run_node.id)
        return False

    # Create standard Phase 1-4 Task
    task = Task(
        project_id=workflow.project_id,
        type=workflow_node.task_type,
        payload=dict(workflow_node.payload),
        status=TaskStatus.QUEUED,
        queued_at=now,
        timeout_seconds=workflow_node.timeout_seconds,
        max_retries=workflow_node.max_retries,
        workflow_run_node_id=run_node.id,
    )
    db.add(task)
    db.flush()

    # Link task to run_node
    db.execute(
        update(WorkflowRunNode)
        .where(WorkflowRunNode.id == run_node.id)
        .values(task_id=task.id)
    )
    db.commit()

    publish_task(task.id)
    logger.info(
        "node_dispatched run_node_id=%s node_key=%s task_id=%s task_type=%s",
        run_node.id, workflow_node.node_key, task.id, workflow_node.task_type,
    )
    return True


def start_workflow_run(db: Session, owner: User, workflow_id: UUID) -> WorkflowRun:
    """Instantiate a workflow run and dispatch root nodes."""
    workflow = db.scalar(
        select(Workflow)
        .options(joinedload(Workflow.nodes), joinedload(Workflow.edges))
        .filter_by(id=workflow_id)
    )
    if not workflow:
        raise APIError(status_code=404, code="not_found", message="Workflow not found")

    get_owned_project(db, owner, workflow.project_id)

    now = _now_utc()
    run = WorkflowRun(
        workflow_id=workflow.id,
        status=WorkflowRunStatus.RUNNING,
        failure_policy=workflow.failure_policy,
        started_at=now,
    )
    db.add(run)
    db.flush()

    run_nodes_map: dict[UUID, WorkflowRunNode] = {}
    for node in workflow.nodes:
        run_node = WorkflowRunNode(
            workflow_run_id=run.id,
            workflow_node_id=node.id,
            status=WorkflowRunNodeStatus.PENDING,
        )
        db.add(run_node)
        db.flush()
        run_nodes_map[node.id] = run_node

    has_incoming = {edge.to_node_id for edge in workflow.edges}

    # Dispatch root nodes (nodes without incoming edges)
    for node in workflow.nodes:
        if node.id not in has_incoming:
            _dispatch_node(db, run_nodes_map[node.id], workflow)

    db.commit()

    run_loaded = db.scalar(
        select(WorkflowRun)
        .options(
            joinedload(WorkflowRun.run_nodes).joinedload(WorkflowRunNode.workflow_node)
        )
        .filter_by(id=run.id)
    )
    logger.info("workflow_run_started run_id=%s workflow_id=%s", run.id, workflow.id)
    return run_loaded


# ---------------------------------------------------------------------------
# Dependency Evaluation & Advancement
# ---------------------------------------------------------------------------

def _get_dependency_nodes(db: Session, run_node: WorkflowRunNode, run_id: UUID) -> list[WorkflowRunNode]:
    """Retrieve all predecessor WorkflowRunNode instances for a given run node."""
    stmt = (
        select(WorkflowRunNode)
        .join(WorkflowEdge, WorkflowEdge.from_node_id == WorkflowRunNode.workflow_node_id)
        .where(
            WorkflowEdge.to_node_id == run_node.workflow_node_id,
            WorkflowRunNode.workflow_run_id == run_id,
        )
    )
    return list(db.scalars(stmt).all())


def _evaluate_pending_nodes(db: Session, run: WorkflowRun) -> None:
    """Evaluate all pending nodes in a run, dispatching ready nodes and skipping blocked ones."""
    workflow = db.get(Workflow, run.workflow_id)
    if workflow is None:
        return

    while True:
        changed = False
        pending_nodes = db.scalars(
            select(WorkflowRunNode).where(
                WorkflowRunNode.workflow_run_id == run.id,
                WorkflowRunNode.status == WorkflowRunNodeStatus.PENDING,
            )
        ).all()

        for run_node in pending_nodes:
            dep_nodes = _get_dependency_nodes(db, run_node, run.id)
            if not dep_nodes:
                continue

            dep_statuses = [d.status for d in dep_nodes]

            # If any dependency failed or was skipped -> skip this dependent node
            if any(s in (WorkflowRunNodeStatus.FAILED, WorkflowRunNodeStatus.SKIPPED) for s in dep_statuses):
                res = db.execute(
                    update(WorkflowRunNode)
                    .where(
                        WorkflowRunNode.id == run_node.id,
                        WorkflowRunNode.status == WorkflowRunNodeStatus.PENDING,
                    )
                    .values(
                        status=WorkflowRunNodeStatus.SKIPPED,
                        finished_at=_now_utc(),
                        error_message="Skipped: upstream dependency failed or skipped",
                    )
                )
                if res.rowcount > 0:
                    db.commit()
                    changed = True
                    logger.info("node_skipped run_node_id=%s run_id=%s", run_node.id, run.id)

            # If all dependencies succeeded -> ready to dispatch
            elif all(s == WorkflowRunNodeStatus.SUCCESS for s in dep_statuses):
                if _dispatch_node(db, run_node, workflow):
                    changed = True

        if not changed:
            break


def _check_workflow_completion(db: Session, run: WorkflowRun) -> None:
    """Check if all nodes in a run are terminal and finalize the workflow run."""
    if run.status in (WorkflowRunStatus.SUCCESS, WorkflowRunStatus.FAILED):
        return

    nodes = db.scalars(
        select(WorkflowRunNode).where(WorkflowRunNode.workflow_run_id == run.id)
    ).all()

    if not nodes:
        return

    terminal_statuses = (
        WorkflowRunNodeStatus.SUCCESS,
        WorkflowRunNodeStatus.FAILED,
        WorkflowRunNodeStatus.SKIPPED,
    )

    if all(n.status in terminal_statuses for n in nodes):
        now = _now_utc()
        run.finished_at = now

        if any(n.status == WorkflowRunNodeStatus.FAILED for n in nodes):
            run.status = WorkflowRunStatus.FAILED
            logger.info("workflow_run_failed run_id=%s", run.id)
        elif all(n.status == WorkflowRunNodeStatus.SUCCESS for n in nodes):
            run.status = WorkflowRunStatus.SUCCESS
            logger.info("workflow_run_completed run_id=%s", run.id)
        else:
            # Contains skipped nodes without direct failure (or partial branch completion)
            run.status = WorkflowRunStatus.FAILED
            logger.info("workflow_run_failed_with_skipped run_id=%s", run.id)

        db.commit()


def advance_workflow_after_task(db: Session, run_node_id: UUID, task_status: TaskStatus) -> None:
    """Advance the workflow run state after a task reaches terminal state.

    Called by the worker runtime hook upon final task SUCCESS or FAILURE.
    """
    run_node = db.get(WorkflowRunNode, run_node_id)
    if not run_node:
        return

    run = db.get(WorkflowRun, run_node.workflow_run_id)
    if not run or run.status in (WorkflowRunStatus.SUCCESS, WorkflowRunStatus.FAILED):
        return

    now = _now_utc()

    if task_status == TaskStatus.SUCCESS:
        run_node.status = WorkflowRunNodeStatus.SUCCESS
        run_node.finished_at = now
        logger.info("node_completed run_node_id=%s run_id=%s", run_node.id, run.id)
    elif task_status in (TaskStatus.FAILED, TaskStatus.DEAD_LETTER, TaskStatus.TIMED_OUT):
        run_node.status = WorkflowRunNodeStatus.FAILED
        run_node.finished_at = now
        run_node.error_message = f"Task execution {task_status.value.lower()}"
        logger.warning("node_failed run_node_id=%s run_id=%s status=%s", run_node.id, run.id, task_status.value)

    db.commit()

    # Fail-Fast policy: immediately skip all remaining PENDING nodes and fail run
    if run_node.status == WorkflowRunNodeStatus.FAILED and run.failure_policy == FailurePolicy.FAIL_FAST.value:
        db.execute(
            update(WorkflowRunNode)
            .where(
                WorkflowRunNode.workflow_run_id == run.id,
                WorkflowRunNode.status == WorkflowRunNodeStatus.PENDING,
            )
            .values(
                status=WorkflowRunNodeStatus.SKIPPED,
                finished_at=now,
                error_message="Skipped: fail-fast policy triggered by node failure",
            )
        )
        run.status = WorkflowRunStatus.FAILED
        run.finished_at = now
        run.error_message = "Workflow failed: fail-fast policy triggered by node failure"
        db.commit()
        logger.info("workflow_run_fail_fast run_id=%s", run.id)
        return

    # Evaluate pending nodes and check overall completion
    _evaluate_pending_nodes(db, run)
    _check_workflow_completion(db, run)


def get_workflow_run(db: Session, owner: User, workflow_id: UUID, run_id: UUID) -> WorkflowRun:
    """Retrieve a workflow run with all its node execution statuses."""
    workflow = db.get(Workflow, workflow_id)
    if not workflow:
        raise APIError(status_code=404, code="not_found", message="Workflow not found")

    get_owned_project(db, owner, workflow.project_id)

    run = db.scalar(
        select(WorkflowRun)
        .options(
            joinedload(WorkflowRun.run_nodes).joinedload(WorkflowRunNode.workflow_node)
        )
        .filter_by(id=run_id, workflow_id=workflow_id)
    )
    if not run:
        raise APIError(status_code=404, code="not_found", message="Workflow Run not found")

    return run


def list_workflows(db: Session, owner: User, project_id: UUID | None = None) -> list[Workflow]:
    """List workflow definitions visible to the owner, with graph data for the UI."""
    query = (
        select(Workflow)
        .options(joinedload(Workflow.nodes), joinedload(Workflow.edges))
        .join(Workflow.project)
        .where(Workflow.project.has(owner_id=owner.id))
        .order_by(Workflow.created_at.desc())
    )
    if project_id is not None:
        get_owned_project(db, owner, project_id)
        query = query.where(Workflow.project_id == project_id)
    return list(db.scalars(query).unique().all())


def list_workflow_runs(db: Session, owner: User, workflow_id: UUID, limit: int = 20) -> list[WorkflowRun]:
    """List recent runs for an owned workflow without changing execution state."""
    workflow = db.get(Workflow, workflow_id)
    if not workflow:
        raise APIError(status_code=404, code="not_found", message="Workflow not found")
    get_owned_project(db, owner, workflow.project_id)
    return list(
        db.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.workflow_id == workflow_id)
            .order_by(WorkflowRun.started_at.desc())
            .limit(limit)
        ).all()
    )


def get_workflow(db: Session, owner: User, workflow_id: UUID) -> Workflow:
    """Return one owned definition with its nodes and edges for a read-only graph."""
    workflow = db.scalar(
        select(Workflow)
        .options(joinedload(Workflow.nodes), joinedload(Workflow.edges))
        .where(Workflow.id == workflow_id)
    )
    if not workflow:
        raise APIError(status_code=404, code="not_found", message="Workflow not found")
    get_owned_project(db, owner, workflow.project_id)
    return workflow
