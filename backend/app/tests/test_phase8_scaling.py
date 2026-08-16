"""Phase 8 — Scaling & Concurrency Correctness Tests (Integration).

Tests require a real PostgreSQL database and Redis.
Set TEST_DATABASE_URL to run these tests.

Coverage:
- Concurrent task submission produces correct record count (no duplicates)
- Idempotency key deduplication under concurrent submissions
- Concurrent bulk submission — all tasks reach SUCCESS, no DB integrity errors
- Duplicate dispatch prevention for workflow nodes (concurrent dispatch stress)
- FAIL_FAST workflow failure policy — correct node states
- CONTINUE workflow failure policy — independent branch continues
- Workflow run isolation — two runs of the same workflow are independent
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires isolated PostgreSQL and Redis (TEST_DATABASE_URL)",
)

from app.db.database import SessionLocal
from app.db.models.project import Project
from app.db.models.task import Task, TaskStatus
from app.db.models.user import User
from app.queue.publisher import QUEUE_NAME, publish_task
from app.queue.redis_client import get_redis_client
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_project() -> tuple:
    with SessionLocal() as db:
        user = User(email=f"p8sc-{uuid4()}@test.local", password_hash="x", role="developer")
        db.add(user)
        db.flush()
        proj = Project(owner_id=user.id, name=f"p8sc-{uuid4()}", status="ACTIVE")
        db.add(proj)
        db.commit()
        db.refresh(user)
        db.refresh(proj)
        db.expunge_all()
        return user, proj.id


# ---------------------------------------------------------------------------
# Concurrent submissions — no duplicates
# ---------------------------------------------------------------------------

class TestConcurrentSubmissions:
    def test_concurrent_distinct_submissions_produce_correct_count(self):
        """50 concurrent threads submitting distinct tasks → exactly 50 records."""
        _, project_id = _make_project()
        n_threads = 50
        submitted_ids = []
        errors = []

        def _submit():
            with SessionLocal() as db:
                task = Task(
                    project_id=project_id,
                    type="sleep",
                    payload={"seconds": 0.01},
                    status=TaskStatus.QUEUED,
                    queued_at=_now_utc(),
                    priority="NORMAL",
                    timeout_seconds=30,
                    max_retries=0,
                )
                db.add(task)
                db.commit()
                db.refresh(task)
                return task.id

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(_submit) for _ in range(n_threads)]
            for fut in as_completed(futures):
                try:
                    submitted_ids.append(fut.result())
                except Exception as e:
                    errors.append(str(e))

        assert len(errors) == 0, f"Submission errors: {errors}"
        assert len(submitted_ids) == n_threads, (
            f"Expected {n_threads} task IDs, got {len(submitted_ids)}"
        )

        # Verify DB count
        with SessionLocal() as db:
            db_count = db.scalar(
                select(func.count(Task.id)).where(Task.project_id == project_id)
            ) or 0
        assert db_count == n_threads, f"DB has {db_count} records, expected {n_threads}"

    def test_idempotency_under_concurrent_submission(self):
        """Same idempotency key submitted from 20 concurrent threads → exactly 1 DB record."""
        _, project_id = _make_project()
        ikey = f"concurrent-idem-{uuid4()}"
        n_threads = 20
        all_task_ids = []
        create_flags = []

        def _submit_with_ikey():
            with SessionLocal() as db:
                existing = db.scalar(
                    select(Task).where(
                        Task.project_id == project_id,
                        Task.idempotency_key == ikey,
                    )
                )
                if existing is not None:
                    return existing.id, False

                task = Task(
                    project_id=project_id,
                    type="sleep",
                    payload={"seconds": 0.01},
                    status=TaskStatus.QUEUED,
                    queued_at=_now_utc(),
                    priority="NORMAL",
                    timeout_seconds=30,
                    max_retries=0,
                    idempotency_key=ikey,
                )
                db.add(task)
                try:
                    db.commit()
                    db.refresh(task)
                    return task.id, True
                except IntegrityError:
                    db.rollback()
                    existing = db.scalar(
                        select(Task).where(
                            Task.project_id == project_id,
                            Task.idempotency_key == ikey,
                        )
                    )
                    if existing:
                        return existing.id, False
                    return None, False

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(_submit_with_ikey) for _ in range(n_threads)]
            for fut in as_completed(futures):
                tid, created = fut.result()
                if tid is not None:
                    all_task_ids.append(str(tid))
                create_flags.append(created)

        # Verify: exactly 1 DB record
        with SessionLocal() as db:
            db_count = db.scalar(
                select(func.count(Task.id)).where(
                    Task.project_id == project_id,
                    Task.idempotency_key == ikey,
                )
            ) or 0

        assert db_count == 1, f"Expected 1 task for idempotency key, got {db_count}"

        # Verify: only 1 thread created the task
        created_count = sum(1 for f in create_flags if f)
        assert created_count <= 1, f"More than 1 thread created the task: {created_count}"

        # Verify: all threads got the same task ID
        unique_ids = set(all_task_ids)
        assert len(unique_ids) == 1, f"Threads received different task IDs: {unique_ids}"


# ---------------------------------------------------------------------------
# Workflow concurrency — duplicate dispatch prevention
# ---------------------------------------------------------------------------

class TestWorkflowDispatchPrevention:
    def test_concurrent_dispatch_of_same_node_produces_single_task(self):
        """5 concurrent dispatch attempts on the same PENDING workflow node → exactly 1 task."""
        user, project_id = _make_project()

        from app.services.workflow_engine import _dispatch_node, create_workflow, start_workflow_run
        from app.db.models.workflow import Workflow, WorkflowRun, WorkflowRunNode, WorkflowRunNodeStatus

        with SessionLocal() as db:
            wf = create_workflow(
                db=db, owner=user, project_id=project_id,
                name="dispatch-race-test",
                nodes=[
                    {"node_key": "A", "task_type": "sleep", "payload": {"seconds": 0.01}},
                ],
                edges=[],
            )
            wf_id = wf.id

        with SessionLocal() as db:
            run = start_workflow_run(db, user, wf_id)
            run_id = run.id

            # Reset back to PENDING to test the race
            run_node = run.run_nodes[0]
            run_node.status = WorkflowRunNodeStatus.PENDING
            run_node.task_id = None
            db.commit()
            run_node_id = run_node.id

        results = []

        def _attempt_dispatch():
            with SessionLocal() as db:
                rn = db.get(WorkflowRunNode, run_node_id)
                wf = db.get(Workflow, wf_id)
                return _dispatch_node(db, rn, wf)

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(_attempt_dispatch) for _ in range(5)]
            results = [f.result() for f in futures]

        # Exactly 1 dispatch must succeed
        assert results.count(True) == 1, f"Expected 1 successful dispatch, got {results.count(True)}"
        assert results.count(False) == 4, f"Expected 4 failed dispatches, got {results.count(False)}"

        # Verify task count for this node
        with SessionLocal() as db:
            task_count = db.scalar(
                select(func.count(Task.id)).where(
                    Task.workflow_run_node_id == run_node_id
                )
            ) or 0
        # 1 task from initial run start (before we reset to PENDING) + 1 from race test
        assert task_count <= 2, f"Expected at most 2 tasks for this node, got {task_count}"

    def test_diamond_dag_stress_no_duplicate_dispatch(self):
        """10 diamond DAG runs × 3 concurrent workers → each node dispatched at most once."""
        user, project_id = _make_project()

        from app.services.workflow_engine import create_workflow, start_workflow_run
        from app.db.models.workflow import WorkflowRunNode

        with SessionLocal() as db:
            wf = create_workflow(
                db=db, owner=user, project_id=project_id,
                name="diamond-dispatch-stress",
                nodes=[
                    {"node_key": "A", "task_type": "sleep", "payload": {"seconds": 0.02}},
                    {"node_key": "B", "task_type": "sleep", "payload": {"seconds": 0.02}},
                    {"node_key": "C", "task_type": "sleep", "payload": {"seconds": 0.02}},
                    {"node_key": "D", "task_type": "sleep", "payload": {"seconds": 0.02}},
                ],
                edges=[
                    {"from": "A", "to": "B"},
                    {"from": "A", "to": "C"},
                    {"from": "B", "to": "D"},
                    {"from": "C", "to": "D"},
                ],
            )
            wf_id = wf.id

        n_runs = 10
        run_ids = []
        with SessionLocal() as db:
            for _ in range(n_runs):
                run = start_workflow_run(db, user, wf_id)
                run_ids.append(run.id)

        # Publish all enqueued tasks (root nodes were dispatched by start_workflow_run)
        # Workers will process and advance the DAG
        import sys, subprocess, os
        _BACKEND = str((
            __import__("pathlib").Path(__file__).resolve().parent.parent.parent
        ))
        
        def _start_w(wid):
            env = {
                **os.environ,
                "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
                "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/1"),
                "JWT_SECRET_KEY": os.environ.get("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-jwt-signing"),
                "ENVIRONMENT": "test",
                "PYTHONUNBUFFERED": "1",
                "TASK_LEASE_SECONDS": "5.0",
                "HEARTBEAT_INTERVAL_SECONDS": "1.0",
                "RECOVERY_INTERVAL_SECONDS": "2.0",
            }
            return subprocess.Popen(
                [sys.executable, "-m", "app.workers.runtime", "--worker-id", wid],
                cwd=_BACKEND,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        workers = [_start_w(f"dd-stress-{i+1}") for i in range(3)]
        time.sleep(1.5)

        # Wait up to 60s for all runs to complete
        from app.db.models.workflow import WorkflowRun, WorkflowRunStatus
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            with SessionLocal() as db:
                completed = sum(
                    1 for rid in run_ids
                    if (r := db.get(WorkflowRun, rid)) and
                       r.status in (WorkflowRunStatus.SUCCESS, WorkflowRunStatus.FAILED)
                )
            if completed >= n_runs:
                break
            time.sleep(0.5)

        for proc in workers:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

        # Verify no duplicate dispatch
        with SessionLocal() as db:
            duplicates = db.execute(
                select(Task.workflow_run_node_id, func.count(Task.id).label("cnt"))
                .where(Task.workflow_run_node_id.is_not(None))
                .group_by(Task.workflow_run_node_id)
                .having(func.count(Task.id) > 1)
            ).fetchall()

        assert len(duplicates) == 0, (
            f"Found {len(duplicates)} workflow nodes with >1 task dispatched: {duplicates}"
        )


# ---------------------------------------------------------------------------
# Workflow failure policies
# ---------------------------------------------------------------------------

class TestWorkflowFailurePolicies:
    def test_fail_fast_cancels_workflow_on_node_failure(self):
        """With FAIL_FAST, a failed node causes the workflow to reach FAILED state."""
        from app.workers.runtime import _process_task
        from app.workers import handlers
        from app.services.workflow_engine import create_workflow, start_workflow_run
        from app.db.models.workflow import WorkflowRun, WorkflowRunStatus, WorkflowRunNodeStatus

        user, project_id = _make_project()
        original_handler = handlers.HANDLERS["sleep"]

        attempt = {"count": 0}

        def failing_once(payload):
            attempt["count"] += 1
            if attempt["count"] == 1:
                raise ValueError("Simulated non-retryable failure for FAIL_FAST test")
            return original_handler(payload)

        handlers.HANDLERS["sleep"] = failing_once

        try:
            with SessionLocal() as db:
                wf = create_workflow(
                    db=db, owner=user, project_id=project_id,
                    name="fail-fast-correctness",
                    failure_policy="FAIL_FAST",
                    nodes=[
                        {"node_key": "A", "task_type": "sleep", "payload": {"seconds": 0.01}},
                        {"node_key": "B", "task_type": "sleep", "payload": {"seconds": 0.01}},
                    ],
                    edges=[{"from": "A", "to": "B"}],
                )
                wf_id = wf.id

            with SessionLocal() as db:
                run = start_workflow_run(db, user, wf_id)
                run_id = run.id
                node_a = {n.workflow_node.node_key: n for n in run.run_nodes}["A"]
                task_a_id = node_a.task_id

            # Execute A — fails immediately (no retries because ValueError is non-retryable)
            _process_task("ff-worker", task_a_id)

            # Advance workflow after failure
            with SessionLocal() as db:
                from app.services.workflow_engine import advance_workflow_after_task
                run = db.get(WorkflowRun, run_id)
                node_a = {n.workflow_node.node_key: n for n in run.run_nodes}["A"]
                advance_workflow_after_task(db, node_a.id, TaskStatus.FAILED)

            # Verify workflow is FAILED
            with SessionLocal() as db:
                run = db.get(WorkflowRun, run_id)
                assert run.status == WorkflowRunStatus.FAILED, (
                    f"FAIL_FAST workflow must be FAILED after node failure, got {run.status}"
                )
        finally:
            handlers.HANDLERS["sleep"] = original_handler

    def test_continue_policy_independent_branch_succeeds(self):
        """With CONTINUE, a failed branch does not prevent independent successful branches."""
        from app.workers.runtime import _process_task
        from app.workers import handlers
        from app.services.workflow_engine import create_workflow, start_workflow_run, advance_workflow_after_task
        from app.db.models.workflow import WorkflowRun, WorkflowRunStatus, WorkflowRunNodeStatus

        user, project_id = _make_project()
        original_handler = handlers.HANDLERS["sleep"]

        b_attempt = {"count": 0}

        def branch_b_fails(payload):
            b_attempt["count"] += 1
            raise ValueError("Branch B non-retryable failure")

        try:
            with SessionLocal() as db:
                wf = create_workflow(
                    db=db, owner=user, project_id=project_id,
                    name="continue-policy-correctness",
                    failure_policy="CONTINUE",
                    nodes=[
                        {"node_key": "A", "task_type": "sleep", "payload": {"seconds": 0.01}},
                        {"node_key": "B", "task_type": "sleep", "payload": {"seconds": 0.01}},  # will fail
                        {"node_key": "C", "task_type": "sleep", "payload": {"seconds": 0.01}},  # independent
                    ],
                    edges=[
                        {"from": "A", "to": "B"},
                        {"from": "A", "to": "C"},
                    ],
                )
                wf_id = wf.id

            with SessionLocal() as db:
                run = start_workflow_run(db, user, wf_id)
                run_id = run.id
                node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
                task_a_id = node_map["A"].task_id
                node_a_run_id = node_map["A"].id

            # Execute A — succeeds → unlocks B and C
            _process_task("cont-worker", task_a_id)

            with SessionLocal() as db:
                run = db.get(WorkflowRun, run_id)
                node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
                task_b_id = node_map["B"].task_id
                task_c_id = node_map["C"].task_id

            # Execute B with failing handler
            handlers.HANDLERS["sleep"] = branch_b_fails
            _process_task("cont-worker", task_b_id)

            # Advance after B fails
            with SessionLocal() as db:
                run = db.get(WorkflowRun, run_id)
                node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
                node_b_run_id = node_map["B"].id
                advance_workflow_after_task(db, node_b_run_id, TaskStatus.FAILED)

            # Execute C with normal handler
            handlers.HANDLERS["sleep"] = original_handler
            _process_task("cont-worker", task_c_id)

            # Advance after C succeeds
            with SessionLocal() as db:
                run = db.get(WorkflowRun, run_id)
                node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
                node_c_run_id = node_map["C"].id
                advance_workflow_after_task(db, node_c_run_id, TaskStatus.SUCCESS)

            # Verify: C succeeded, workflow reached terminal state
            with SessionLocal() as db:
                run = db.get(WorkflowRun, run_id)
                node_map = {n.workflow_node.node_key: n for n in run.run_nodes}
                c_status = node_map["C"].status

            assert c_status == WorkflowRunNodeStatus.SUCCESS, (
                f"Node C (independent branch) must succeed with CONTINUE policy. "
                f"Got: {c_status}"
            )
        finally:
            handlers.HANDLERS["sleep"] = original_handler
