"""Phase 5 tests: Workflow DAG Execution, Dependencies & Parallel Branches.

Tests:
- Linear execution (A -> B -> C)
- Root nodes dispatched, dependent nodes blocked initially
- Prerequisite unlocking upon dependency SUCCESS
- Diamond DAG execution (A -> B, C -> D)
- Multi-worker parallel branch completion
"""

import os
from uuid import UUID
import pytest

from app.db.database import SessionLocal
from app.db.models.task import Task, TaskStatus
from app.db.models.workflow import WorkflowRun, WorkflowRunNode, WorkflowRunNodeStatus, WorkflowRunStatus
from app.tests.conftest import auth_headers, register_and_token
from app.workers.runtime import _process_task

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires isolated PostgreSQL and Redis",
)


def _setup_project(client):
    token = register_and_token(client, email="executor@example.com")
    res = client.post("/v1/projects", headers=auth_headers(token), json={"name": "Execution Project"})
    return token, res.json()["id"]


def _run_all_queued_tasks(worker_id: str = "worker-1", max_iterations: int = 20):
    """Drain and execute all currently QUEUED tasks synchronously."""
    for _ in range(max_iterations):
        with SessionLocal() as db:
            queued_tasks = db.scalars(
                select(Task.id).where(Task.status == TaskStatus.QUEUED)
            ).all()

        if not queued_tasks:
            break

        for t_id in queued_tasks:
            _process_task(worker_id, t_id)


def test_linear_workflow_a_b_c_execution(client):
    """Linear workflow A -> B -> C executes in sequence to full SUCCESS."""
    token, project_id = _setup_project(client)

    wf_res = client.post(
        "/v1/workflows",
        headers=auth_headers(token),
        json={
            "project_id": project_id,
            "name": "Linear A->B->C",
            "nodes": [
                {"node_key": "A", "task_type": "sleep", "payload": {"seconds": 0.01}},
                {"node_key": "B", "task_type": "sleep", "payload": {"seconds": 0.01}},
                {"node_key": "C", "task_type": "sleep", "payload": {"seconds": 0.01}},
            ],
            "edges": [
                {"from": "A", "to": "B"},
                {"from": "B", "to": "C"},
            ],
        },
    )
    wf_id = wf_res.json()["id"]

    run_res = client.post(f"/v1/workflows/{wf_id}/run", headers=auth_headers(token))
    run_id = run_res.json()["id"]

    # Step 1: Only A should have an active task created initially
    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
        assert node_map["A"].status == WorkflowRunNodeStatus.RUNNING
        assert node_map["A"].task_id is not None
        assert node_map["B"].status == WorkflowRunNodeStatus.PENDING
        assert node_map["B"].task_id is None
        assert node_map["C"].status == WorkflowRunNodeStatus.PENDING

        # Execute task A
        task_a_id = node_map["A"].task_id

    _process_task("worker-1", task_a_id)

    # Step 2: A is SUCCESS, B is RUNNING (task created), C is PENDING
    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
        assert node_map["A"].status == WorkflowRunNodeStatus.SUCCESS
        assert node_map["B"].status == WorkflowRunNodeStatus.RUNNING
        assert node_map["B"].task_id is not None
        assert node_map["C"].status == WorkflowRunNodeStatus.PENDING

        task_b_id = node_map["B"].task_id

    # Execute task B
    _process_task("worker-1", task_b_id)

    # Step 3: B is SUCCESS, C is RUNNING
    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
        assert node_map["B"].status == WorkflowRunNodeStatus.SUCCESS
        assert node_map["C"].status == WorkflowRunNodeStatus.RUNNING

        task_c_id = node_map["C"].task_id

    # Execute task C
    _process_task("worker-1", task_c_id)

    # Step 4: All nodes SUCCESS, Run SUCCESS
    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
        assert node_map["C"].status == WorkflowRunNodeStatus.SUCCESS
        assert run.status == WorkflowRunStatus.SUCCESS
        assert run.finished_at is not None


def test_diamond_workflow_parallel_branches(client):
    """Diamond DAG (A -> B, C -> D): B & C run in parallel, D waits for both."""
    token, project_id = _setup_project(client)

    wf_res = client.post(
        "/v1/workflows",
        headers=auth_headers(token),
        json={
            "project_id": project_id,
            "name": "Diamond DAG",
            "nodes": [
                {"node_key": "A", "task_type": "sleep", "payload": {"seconds": 0.01}},
                {"node_key": "B", "task_type": "sleep", "payload": {"seconds": 0.01}},
                {"node_key": "C", "task_type": "sleep", "payload": {"seconds": 0.01}},
                {"node_key": "D", "task_type": "sleep", "payload": {"seconds": 0.01}},
            ],
            "edges": [
                {"from": "A", "to": "B"},
                {"from": "A", "to": "C"},
                {"from": "B", "to": "D"},
                {"from": "C", "to": "D"},
            ],
        },
    )
    wf_id = wf_res.json()["id"]

    run_res = client.post(f"/v1/workflows/{wf_id}/run", headers=auth_headers(token))
    run_id = run_res.json()["id"]

    # 1. Complete A
    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
        task_a_id = node_map["A"].task_id

    _process_task("worker-1", task_a_id)

    # 2. Both B and C must now be RUNNING with tasks
    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
        assert node_map["A"].status == WorkflowRunNodeStatus.SUCCESS
        assert node_map["B"].status == WorkflowRunNodeStatus.RUNNING
        assert node_map["C"].status == WorkflowRunNodeStatus.RUNNING
        assert node_map["D"].status == WorkflowRunNodeStatus.PENDING
        assert node_map["D"].task_id is None

        task_b_id = node_map["B"].task_id
        task_c_id = node_map["C"].task_id

    # 3. Complete B only -> D must still be PENDING because C has not finished
    _process_task("worker-1", task_b_id)

    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
        assert node_map["B"].status == WorkflowRunNodeStatus.SUCCESS
        assert node_map["C"].status == WorkflowRunNodeStatus.RUNNING
        assert node_map["D"].status == WorkflowRunNodeStatus.PENDING

    # 4. Complete C -> D unlocks and becomes RUNNING
    _process_task("worker-2", task_c_id)

    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
        assert node_map["C"].status == WorkflowRunNodeStatus.SUCCESS
        assert node_map["D"].status == WorkflowRunNodeStatus.RUNNING
        task_d_id = node_map["D"].task_id

    # 5. Complete D -> Workflow reaches SUCCESS
    _process_task("worker-1", task_d_id)

    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        assert run.status == WorkflowRunStatus.SUCCESS
        assert run.finished_at is not None
