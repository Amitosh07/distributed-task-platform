"""Phase 2 — worker integration tests.

Tests the complete task lifecycle through the worker runtime using real
PostgreSQL (workflow_platform_test) and real Redis (DB 1).

Tests cover:
- Worker processes a task end-to-end: QUEUED → RUNNING → SUCCESS
- Worker handles FAILED tasks correctly
- Worker continues after a single task failure
- Worker processes multiple tasks sequentially
- Worker skips tasks not in QUEUED state (defensive check)
- Worker does not execute tasks in terminal states
"""

import os
import time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models.task import Task, TaskStatus

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires isolated PostgreSQL and Redis",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(db, project_id, task_type: str = "sleep", payload: dict = None) -> Task:
    """Insert a QUEUED task directly into the test DB."""
    from datetime import datetime, timezone
    task = Task(
        project_id=project_id,
        type=task_type,
        payload=payload or {"seconds": 0.01},
        status=TaskStatus.QUEUED,
        queued_at=datetime.now(tz=timezone.utc),
        priority="NORMAL",
        timeout_seconds=30,
        max_retries=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _make_project_and_task(task_type: str = "sleep", payload: dict = None):
    """Create a project and a QUEUED task in the test DB. Returns (project_id, task_id)."""
    from app.db.models.project import Project
    from app.db.models.user import User
    with SessionLocal() as db:
        user = User(email=f"worker-test-{uuid4()}@example.com", password_hash="x", role="developer")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, name=f"proj-{uuid4()}", status="ACTIVE")
        db.add(project)
        db.flush()
        task = _make_task(db, project.id, task_type=task_type, payload=payload)
        return project.id, task.id


def _load_task(task_id) -> Task:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        db.expunge(task)
        return task


def _wait_for_status(task_id, target_status: TaskStatus, timeout: float = 10.0) -> Task:
    """Poll the DB until the task reaches target_status or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = _load_task(task_id)
        if task.status == target_status:
            return task
        time.sleep(0.1)
    task = _load_task(task_id)
    raise AssertionError(
        f"Task {task_id} did not reach {target_status} within {timeout}s. "
        f"Current status: {task.status}"
    )


# ---------------------------------------------------------------------------
# Worker _process_task unit tests (run synchronously without the loop)
# ---------------------------------------------------------------------------

class TestWorkerProcessTask:
    """Test _process_task directly so tests run fast without a background thread."""

    def test_sleep_task_reaches_success(self):
        """A valid sleep task transitions QUEUED → RUNNING → SUCCESS."""
        _project_id, task_id = _make_project_and_task("sleep", {"seconds": 0.01})

        from app.workers.runtime import _process_task
        _process_task(task_id)

        task = _load_task(task_id)
        assert task.status == TaskStatus.SUCCESS
        assert task.started_at is not None
        assert task.finished_at is not None
        assert task.result_summary is not None
        assert task.result_summary["message"] == "slept successfully"
        assert task.attempt_count == 1

    def test_invalid_payload_reaches_failed(self):
        """A sleep task with invalid payload transitions QUEUED → RUNNING → FAILED."""
        _project_id, task_id = _make_project_and_task("sleep", {"seconds": -1})

        from app.workers.runtime import _process_task
        _process_task(task_id)

        task = _load_task(task_id)
        assert task.status == TaskStatus.FAILED
        assert task.error_message is not None
        assert task.finished_at is not None

    def test_unknown_task_type_reaches_failed(self):
        """An unknown task type transitions to FAILED without crashing the worker."""
        _project_id, task_id = _make_project_and_task("unknown_type_xyz", {})

        from app.workers.runtime import _process_task
        _process_task(task_id)

        task = _load_task(task_id)
        assert task.status == TaskStatus.FAILED
        assert "unknown_type_xyz" in (task.error_message or "").lower() or "unknown" in (task.error_message or "").lower()

    def test_worker_continues_after_failure(self):
        """After a failed task, the worker can still process a subsequent task."""
        _pid, failing_id = _make_project_and_task("sleep", {"seconds": -99})
        _pid, success_id = _make_project_and_task("sleep", {"seconds": 0.01})

        from app.workers.runtime import _process_task
        _process_task(failing_id)   # This should fail
        _process_task(success_id)   # This should succeed

        failed_task = _load_task(failing_id)
        success_task = _load_task(success_id)
        assert failed_task.status == TaskStatus.FAILED
        assert success_task.status == TaskStatus.SUCCESS

    def test_skips_task_already_in_terminal_state(self):
        """Worker skips a task that is already SUCCESS (defensive duplicate check)."""
        _project_id, task_id = _make_project_and_task("sleep", {"seconds": 0.01})

        from app.workers.runtime import _process_task
        # First execution → SUCCESS
        _process_task(task_id)
        task_after_first = _load_task(task_id)
        assert task_after_first.status == TaskStatus.SUCCESS

        # Second execution → should skip without changing state
        _process_task(task_id)
        task_after_second = _load_task(task_id)
        assert task_after_second.status == TaskStatus.SUCCESS
        # attempt_count must not have incremented
        assert task_after_second.attempt_count == task_after_first.attempt_count

    def test_skips_nonexistent_task(self):
        """_process_task silently skips a task ID that does not exist in the DB."""
        from app.workers.runtime import _process_task
        non_existent = uuid4()
        # Must not raise
        _process_task(non_existent)

    def test_csv_stats_task_succeeds(self):
        """csv_stats handler works end-to-end through the worker."""
        csv_data = "col1,col2\nval1,val2\nval3,val4"
        _project_id, task_id = _make_project_and_task("csv_stats", {"csv_data": csv_data})

        from app.workers.runtime import _process_task
        _process_task(task_id)

        task = _load_task(task_id)
        assert task.status == TaskStatus.SUCCESS
        assert task.result_summary["row_count"] == 2
        assert task.result_summary["column_count"] == 2

    def test_multiple_tasks_processed_sequentially(self):
        """Worker processes multiple tasks in sequence; all reach SUCCESS."""
        task_ids = []
        for _ in range(3):
            _pid, tid = _make_project_and_task("sleep", {"seconds": 0.01})
            task_ids.append(tid)

        from app.workers.runtime import _process_task
        for tid in task_ids:
            _process_task(tid)

        for tid in task_ids:
            task = _load_task(tid)
            assert task.status == TaskStatus.SUCCESS
