"""Phase 5 tests: Workflow Failure Policies (FAIL_FAST vs. CONTINUE).

Tests:
- FAIL_FAST policy immediately skips all PENDING nodes and fails the run.
- CONTINUE policy allows independent parallel branches to complete while skipping dependent nodes.
- Downstream nodes of failed steps are cleanly SKIPPED.
"""

import os
from uuid import UUID
import pytest

from app.db.database import SessionLocal
from app.db.models.workflow import WorkflowRun, WorkflowRunNodeStatus, WorkflowRunStatus
from app.tests.conftest import auth_headers, register_and_token
from app.workers.runtime import _process_task

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires isolated PostgreSQL and Redis",
)


def _setup_project(client):
    token = register_and_token(client, email="policy_tester@example.com")
    res = client.post("/v1/projects", headers=auth_headers(token), json={"name": "Policy Project"})
    return token, res.json()["id"]


def test_fail_fast_policy_skips_all_pending(client):
    """Under FAIL_FAST, when a node fails, all PENDING nodes are SKIPPED and run FAILS immediately."""
    token, project_id = _setup_project(client)

    # Workflow: A -> B (fails) -> C
    wf_res = client.post(
        "/v1/workflows",
        headers=auth_headers(token),
        json={
            "project_id": project_id,
            "name": "Fail-Fast Pipeline",
            "failure_policy": "FAIL_FAST",
            "nodes": [
                {"node_key": "A", "task_type": "sleep", "payload": {"seconds": 0.01}},
                # csv_stats with invalid payload causes NonRetryableError -> immediate task FAILURE
                {"node_key": "B", "task_type": "csv_stats", "payload": {"csv_data": 12345}},  # non-string -> ValueError
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

    # 1. Complete A
    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
        task_a_id = node_map["A"].task_id

    _process_task("worker-1", task_a_id)

    # 2. B is now RUNNING. Process B (which fails)
    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
        assert node_map["B"].status == WorkflowRunNodeStatus.RUNNING
        task_b_id = node_map["B"].task_id

    _process_task("worker-1", task_b_id)

    # 3. Under FAIL_FAST: B is FAILED, C is SKIPPED, Workflow is FAILED
    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
        assert node_map["A"].status == WorkflowRunNodeStatus.SUCCESS
        assert node_map["B"].status == WorkflowRunNodeStatus.FAILED
        assert node_map["C"].status == WorkflowRunNodeStatus.SKIPPED
        assert run.status == WorkflowRunStatus.FAILED
        assert run.finished_at is not None


def test_continue_policy_independent_branch_proceeds(client):
    """Under CONTINUE, if branch B fails, independent branch C still executes to SUCCESS, while D is SKIPPED."""
    token, project_id = _setup_project(client)

    # Diamond: A -> B (fails), C (succeeds) -> D (depends on B & C)
    wf_res = client.post(
        "/v1/workflows",
        headers=auth_headers(token),
        json={
            "project_id": project_id,
            "name": "Continue Pipeline",
            "failure_policy": "CONTINUE",
            "nodes": [
                {"node_key": "A", "task_type": "sleep", "payload": {"seconds": 0.01}},
                {"node_key": "B", "task_type": "csv_stats", "payload": {"csv_data": 9999}},  # will fail
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
        task_a_id = {n.workflow_node.node_key: n for n in run.run_nodes}["A"].task_id

    _process_task("worker-1", task_a_id)

    # 2. Both B and C are RUNNING. Process B (fails)
    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
        task_b_id = node_map["B"].task_id
        task_c_id = node_map["C"].task_id

    _process_task("worker-1", task_b_id)

    # Under CONTINUE: B is FAILED, D is SKIPPED (its dep B failed), but C is STILL RUNNING!
    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
        assert node_map["B"].status == WorkflowRunNodeStatus.FAILED
        assert node_map["C"].status == WorkflowRunNodeStatus.RUNNING
        assert node_map["D"].status == WorkflowRunNodeStatus.SKIPPED
        assert run.status == WorkflowRunStatus.RUNNING  # Not finished yet because C is running

    # 3. Now process C (succeeds)
    _process_task("worker-2", task_c_id)

    # Workflow is now finished as FAILED (since B failed)
    with SessionLocal() as db:
        run = db.get(WorkflowRun, UUID(run_id))
        node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
        assert node_map["C"].status == WorkflowRunNodeStatus.SUCCESS
        assert node_map["D"].status == WorkflowRunNodeStatus.SKIPPED
        assert run.status == WorkflowRunStatus.FAILED
        assert run.finished_at is not None
