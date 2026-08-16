"""Phase 5 tests: Workflow Concurrency, Duplicate Dispatch Prevention & Run Isolation.

Tests:
- Atomic conditional state transition prevents duplicate task dispatch for the same node.
- Complete isolation between separate runs of the same workflow definition.
- Intermediate retry attempts do NOT unlock downstream nodes.
- Downstream nodes unlock only when task reaches final SUCCESS after retry.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID
import pytest
from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models.task import Task, TaskStatus
from app.db.models.workflow import Workflow, WorkflowRun, WorkflowRunNode, WorkflowRunNodeStatus, WorkflowRunStatus
from app.services.workflow_engine import _dispatch_node
from app.tests.conftest import auth_headers, register_and_token
from app.workers.runtime import _process_task
from app.workers import handlers

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires isolated PostgreSQL and Redis",
)


def _setup_project(client):
    token = register_and_token(client, email="concurrency_tester@example.com")
    res = client.post("/v1/projects", headers=auth_headers(token), json={"name": "Concurrency Project"})
    return token, res.json()["id"]


def test_duplicate_node_dispatch_prevented(client):
    """Concurrent calls to dispatch the same PENDING node must result in exactly 1 Task created."""
    token, project_id = _setup_project(client)

    wf_res = client.post(
        "/v1/workflows",
        headers=auth_headers(token),
        json={
            "project_id": project_id,
            "name": "Race Test Workflow",
            "nodes": [
                {"node_key": "step1", "task_type": "sleep", "payload": {"seconds": 0.01}},
            ],
            "edges": [],
        },
    )
    wf_id = wf_res.json()["id"]

    run_res = client.post(f"/v1/workflows/{wf_id}/run", headers=auth_headers(token))
    run_id = run_res.json()["id"]

    # Initial start created 1 task. Let's reset run node to PENDING manually and test race
    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        run_node = run.run_nodes[0]
        run_node.status = WorkflowRunNodeStatus.PENDING
        run_node.task_id = None
        db.commit()

        run_node_id = run_node.id
        workflow_id = run.workflow_id

        # Attempt concurrent dispatch from 5 threads
        results = []
        def attempt_dispatch():
            with SessionLocal() as thread_db:
                rn = thread_db.get(WorkflowRunNode, run_node_id)
                wf = thread_db.get(Workflow, workflow_id)
                return _dispatch_node(thread_db, rn, wf)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(attempt_dispatch) for _ in range(5)]
            results = [f.result() for f in futures]

        # Exactly ONE dispatch must return True; other 4 must return False
        assert results.count(True) == 1
        assert results.count(False) == 4

        # Verify only 1 new task was linked to this node
        tasks = db.scalars(
            select(Task).where(Task.workflow_run_node_id == run_node.id)
        ).all()
        assert len(tasks) == 2  # 1 from initial run creation, 1 from race test


def test_two_workflow_runs_are_completely_isolated(client):
    """Two executions of the same workflow definition have independent state."""
    token, project_id = _setup_project(client)

    wf_res = client.post(
        "/v1/workflows",
        headers=auth_headers(token),
        json={
            "project_id": project_id,
            "name": "Isolated Workflow",
            "nodes": [
                {"node_key": "A", "task_type": "sleep", "payload": {"seconds": 0.01}},
                {"node_key": "B", "task_type": "sleep", "payload": {"seconds": 0.01}},
            ],
            "edges": [{"from": "A", "to": "B"}],
        },
    )
    wf_id = wf_res.json()["id"]

    # Start Run 1 and Run 2
    run1_res = client.post(f"/v1/workflows/{wf_id}/run", headers=auth_headers(token))
    run2_res = client.post(f"/v1/workflows/{wf_id}/run", headers=auth_headers(token))

    run1_id = run1_res.json()["id"]
    run2_id = run2_res.json()["id"]
    assert run1_id != run2_id

    # Execute A for Run 1 only
    with SessionLocal() as db:
        run1 = db.get(WorkflowRun, UUID(run1_id))
        task_a1_id = {n.workflow_node.node_key: n for n in run1.run_nodes}["A"].task_id

    _process_task("worker-1", task_a1_id)

    # In Run 1: A is SUCCESS, B is RUNNING
    # In Run 2: A is still RUNNING, B is still PENDING
    with SessionLocal() as db:
        run1 = db.get(WorkflowRun, UUID(run1_id))
        run2 = db.get(WorkflowRun, UUID(run2_id))

        r1_map = {n.workflow_node.node_key: n for n in run1.run_nodes}
        r2_map = {n.workflow_node.node_key: n for n in run2.run_nodes}

        assert r1_map["A"].status == WorkflowRunNodeStatus.SUCCESS
        assert r1_map["B"].status == WorkflowRunNodeStatus.RUNNING

        assert r2_map["A"].status == WorkflowRunNodeStatus.RUNNING
        assert r2_map["B"].status == WorkflowRunNodeStatus.PENDING


def test_retry_does_not_unlock_downstream(client, monkeypatch):
    """An intermediate failure on a retrying task does NOT unlock downstream nodes."""
    token, project_id = _setup_project(client)

    wf_res = client.post(
        "/v1/workflows",
        headers=auth_headers(token),
        json={
            "project_id": project_id,
            "name": "Retry Pipeline",
            "nodes": [
                # max_retries=2 with retryable exception on first attempt
                {"node_key": "A", "task_type": "sleep", "payload": {"seconds": 0.01}, "max_retries": 2},
                {"node_key": "B", "task_type": "sleep", "payload": {"seconds": 0.01}},
            ],
            "edges": [{"from": "A", "to": "B"}],
        },
    )
    wf_id = wf_res.json()["id"]

    run_res = client.post(f"/v1/workflows/{wf_id}/run", headers=auth_headers(token))
    run_id = run_res.json()["id"]

    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        node_a = {n.workflow_node.node_key: n for n in run.run_nodes}["A"]
        task_a_id = node_a.task_id

    # 1. Monkeypatch handler to raise a retryable RuntimeError on attempt 1
    attempt_count = 0

    def transient_sleep_handler(payload):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            raise RuntimeError("Transient connection reset")
        return {"message": "slept", "seconds": payload.get("seconds", 0.01)}

    monkeypatch.setitem(handlers.HANDLERS, "sleep", transient_sleep_handler)

    # Process task A attempt 1 (fails with retryable RuntimeError -> enters retry QUEUED)
    _process_task("worker-1", task_a_id)

    # Task is now in retry wait / QUEUED state
    with SessionLocal() as db:
        task_a = db.get(Task, task_a_id)
        run = db.get(WorkflowRun, UUID(run_id))
        node_map = {n.workflow_node.node_key: n for n in run.run_nodes}

        assert task_a.status == TaskStatus.QUEUED
        assert task_a.attempt_count == 1
        # Downstream node B must REMAIN PENDING!
        assert node_map["B"].status == WorkflowRunNodeStatus.PENDING
        assert node_map["B"].task_id is None
        # Node A is still RUNNING
        assert node_map["A"].status == WorkflowRunNodeStatus.RUNNING

    # 2. Process task A attempt 2 (succeeds)
    _process_task("worker-1", task_a_id)

    # Now A is SUCCESS, and downstream B unlocks!
    with SessionLocal() as db:
        task_a = db.get(Task, task_a_id)
        run = db.get(WorkflowRun, UUID(run_id))
        node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
        assert task_a.status == TaskStatus.SUCCESS
        assert node_map["A"].status == WorkflowRunNodeStatus.SUCCESS
        assert node_map["B"].status == WorkflowRunNodeStatus.RUNNING
        assert node_map["B"].task_id is not None
