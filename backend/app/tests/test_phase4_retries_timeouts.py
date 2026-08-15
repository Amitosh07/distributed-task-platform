"""Phase 4 — Task Execution Timeouts, Retries & Error Classification tests.

Tests cover:
- Task timeout raises TaskTimeoutError and enters retry flow without crashing worker.
- max_retries = 0 produces exactly 1 execution attempt and fails.
- Retryable failures are retried up to max_retries with incremented attempt_count.
- Non-retryable errors (e.g. invalid payload/ValueError) fail immediately without retry.
- Exponential backoff delay calculation.
"""

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.db.database import SessionLocal
from app.db.models.project import Project
from app.db.models.task import Task, TaskStatus
from app.db.models.user import User
from app.services.retry_policy import calculate_backoff_delay, is_retryable_error
from app.workers.exceptions import NonRetryableError, TaskTimeoutError
from app.workers.runtime import _process_task

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires isolated PostgreSQL and Redis",
)

_TEST_WORKER_ID = "test-worker-retries"


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_project_and_task(
    task_type: str = "sleep",
    payload: dict | None = None,
    timeout_seconds: int = 30,
    max_retries: int = 3,
) -> tuple:
    payload = payload or {"seconds": 0.05}
    with SessionLocal() as db:
        user = User(email=f"retry-test-{uuid4()}@example.com", password_hash="x", role="developer")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, name=f"proj-{uuid4()}", status="ACTIVE")
        db.add(project)
        db.flush()
        task = Task(
            project_id=project.id,
            type=task_type,
            payload=payload,
            status=TaskStatus.QUEUED,
            queued_at=_now_utc(),
            priority="NORMAL",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return project.id, task.id


def _load_task(task_id) -> Task:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        db.expunge(task)
        return task


class TestRetryPolicy:
    def test_backoff_calculation(self):
        """Exponential backoff doubles with each attempt and respects max."""
        assert calculate_backoff_delay(1, base_seconds=1.0, max_seconds=30.0) == 1.0
        assert calculate_backoff_delay(2, base_seconds=1.0, max_seconds=30.0) == 2.0
        assert calculate_backoff_delay(3, base_seconds=1.0, max_seconds=30.0) == 4.0
        assert calculate_backoff_delay(10, base_seconds=1.0, max_seconds=30.0) == 30.0

    def test_error_classification(self):
        """NonRetryableError and ValueError are not retryable; others are."""
        assert is_retryable_error(ValueError("invalid payload")) is False
        assert is_retryable_error(NonRetryableError("unknown type")) is False
        assert is_retryable_error(TaskTimeoutError("timed out")) is True
        assert is_retryable_error(RuntimeError("connection drop")) is True


class TestTaskTimeouts:
    def test_task_timeout_triggers_retry_flow(self):
        """A task exceeding timeout_seconds is timed out and scheduled for retry."""
        _, task_id = _make_project_and_task(
            "sleep",
            {"seconds": 1.5},
            timeout_seconds=1,
            max_retries=2,
        )

        _process_task(_TEST_WORKER_ID, task_id)

        task = _load_task(task_id)
        # Attempt 1 failed with timeout -> requeued since max_retries=2
        assert task.status == TaskStatus.QUEUED
        assert task.attempt_count == 1
        assert "timed out" in (task.error_message or "").lower() or "timeout" in (task.error_message or "").lower()

    def test_timeout_unblocks_worker_without_waiting_for_underlying_thread(self):
        """Worker does not wait for a hung/slow thread to finish before returning from _process_task.

        If a task specifies a 3.0s sleep but timeout_seconds=1, _process_task returns in ~1s,
        not 3s.
        """
        import time
        _, task_id = _make_project_and_task(
            "sleep",
            {"seconds": 3.0},
            timeout_seconds=1,
            max_retries=0,
        )

        t_start = time.monotonic()
        _process_task(_TEST_WORKER_ID, task_id)
        elapsed = time.monotonic() - t_start

        # Should unblock within ~1.5s, well before the 3.0s sleep finishes
        assert elapsed < 2.2, f"Expected _process_task to return promptly on timeout, took {elapsed:.2f}s"

        task = _load_task(task_id)
        assert task.status == TaskStatus.FAILED



class TestRetryFlow:
    def test_max_retries_zero_fails_on_first_attempt(self):
        """With max_retries=0, a single failure transitions directly to FAILED."""
        _, task_id = _make_project_and_task(
            "sleep",
            {"seconds": -1},  # invalid -> fails
            max_retries=0,
        )

        _process_task(_TEST_WORKER_ID, task_id)

        task = _load_task(task_id)
        assert task.status == TaskStatus.FAILED
        assert task.attempt_count == 1
        assert task.finished_at is not None

    def test_non_retryable_error_fails_immediately_without_retry(self):
        """ValueError (e.g. invalid payload parameter) fails immediately even if max_retries > 0."""
        _, task_id = _make_project_and_task(
            "sleep",
            {"seconds": -99},
            max_retries=3,
        )

        _process_task(_TEST_WORKER_ID, task_id)

        task = _load_task(task_id)
        # Non-retryable error -> directly FAILED despite max_retries=3
        assert task.status == TaskStatus.FAILED
        assert task.attempt_count == 1

    def test_retry_increments_attempts_until_exhausted(self):
        """Task retries up to max_retries and then transitions to FAILED."""
        # Custom mock transient failure by using timeout
        _, task_id = _make_project_and_task(
            "sleep",
            {"seconds": 1.0},
            timeout_seconds=1,
            max_retries=1,  # 1 initial + 1 retry = 2 attempts total
        )

        with SessionLocal() as db:
            task = db.get(Task, task_id)
            task.payload = {"seconds": 1.5}
            task.timeout_seconds = 1
            db.commit()

        # Attempt 1 -> times out -> requeued
        _process_task(_TEST_WORKER_ID, task_id)
        t1 = _load_task(task_id)
        assert t1.status == TaskStatus.QUEUED
        assert t1.attempt_count == 1

        # Attempt 2 (retry) -> times out -> exhausted (attempt 2 > max_retries 1) -> FAILED
        _process_task(_TEST_WORKER_ID, task_id)
        t2 = _load_task(task_id)
        assert t2.status == TaskStatus.FAILED
        assert t2.attempt_count == 2
