"""Phase 8 — Fault Injection Correctness Tests (Integration).

Tests require a real PostgreSQL database and Redis.
Set TEST_DATABASE_URL to run these tests.

Coverage:
- Worker crash → lease expiry → automatic recovery → SUCCESS
- Timeout: task exceeds timeout → TIMED_OUT → retry policy applied
- Retry on transient error: task fails once → retry → SUCCESS
- Permanent failure: non-retryable / exhausted retries → FAILED
- Redis publish failure: task stays QUEUED in PostgreSQL (durability)
- Recovery concurrency: two recovery workers race → exactly one wins
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
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
from app.services.recovery import detect_stale_workers, recover_stale_tasks
from app.workers.runtime import _atomic_claim, _transition_to_success


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_task(
    task_type: str = "sleep",
    payload: dict | None = None,
    timeout_seconds: int = 30,
    max_retries: int = 3,
) -> tuple[UUID, UUID]:
    payload = payload or {"seconds": 0.05}
    with SessionLocal() as db:
        user = User(email=f"p8fi-{uuid4()}@test.local", password_hash="x", role="developer")
        db.add(user)
        db.flush()
        proj = Project(owner_id=user.id, name=f"p8fi-{uuid4()}", status="ACTIVE")
        db.add(proj)
        db.flush()
        task = Task(
            project_id=proj.id,
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
        return proj.id, task.id


def _load_task(task_id: UUID) -> Task:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        db.expunge(task)
        return task


def _start_worker(worker_id: str, extra_env: dict | None = None) -> tuple[subprocess.Popen, tempfile.SpooledTemporaryFile]:
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    env = {
        **os.environ,
        "TEST_DATABASE_URL": os.environ["TEST_DATABASE_URL"],
        "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
        "TEST_REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/1"),
        "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/1"),
        "JWT_SECRET_KEY": os.environ.get("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-jwt-signing"),
        "ENVIRONMENT": "test",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "TASK_LEASE_SECONDS": "3.0",
        "HEARTBEAT_INTERVAL_SECONDS": "1.0",
        "RECOVERY_INTERVAL_SECONDS": "2.0",
        "WORKER_STALE_THRESHOLD_SECONDS": "5.0",
        **(extra_env or {}),
    }
    log = tempfile.TemporaryFile(mode="w+b")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.workers.runtime", "--worker-id", worker_id],
        cwd=backend_dir,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    return proc, log


def _stop_worker(handle: tuple[subprocess.Popen, object], timeout: float = 5.0) -> str:
    proc, log_file = handle
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    log_file.seek(0)
    output = log_file.read().decode("utf-8", errors="replace")
    log_file.close()
    return output


def _wait_terminal(task_id: UUID, timeout: float = 30.0) -> Task:
    terminal = {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.TIMED_OUT, TaskStatus.DEAD_LETTER}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = _load_task(task_id)
        if task.status in terminal:
            return task
        time.sleep(0.1)
    task = _load_task(task_id)
    raise AssertionError(
        f"Task {task_id} did not reach terminal state in {timeout}s. "
        f"Current status: {task.status}"
    )


# ---------------------------------------------------------------------------
# Test: Worker crash → lease expiry → recovery → SUCCESS
# ---------------------------------------------------------------------------

class TestWorkerCrashRecovery:
    def test_crash_recovery_end_to_end(self):
        """Worker A crashes during execution → lease expires → Worker B recovers → SUCCESS."""
        _, task_id = _make_task("sleep", {"seconds": 0.1}, max_retries=1)
        publish_task(task_id)

        # Start Worker A
        worker_a = _start_worker("crash-A")
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            task = _load_task(task_id)
            if task.status == TaskStatus.RUNNING:
                break
            time.sleep(0.05)

        # Hard-kill Worker A (simulate crash)
        worker_a[0].kill()
        worker_a[0].wait()
        worker_a[1].close()

        # Expire lease immediately
        with SessionLocal() as db:
            t = db.get(Task, task_id)
            if t and t.status == TaskStatus.RUNNING:
                t.lease_expires_at = _now_utc() - timedelta(seconds=5)
                db.commit()

        # Start Worker B
        worker_b = _start_worker("recovery-B")
        try:
            task = _wait_terminal(task_id, timeout=25.0)
            assert task.status == TaskStatus.SUCCESS, (
                f"Expected SUCCESS, got {task.status}. "
                f"Error: {task.error_message}"
            )
            assert task.attempt_count >= 2, (
                f"Expected at least 2 attempts (original + recovery), got {task.attempt_count}"
            )
        finally:
            _stop_worker(worker_b)

    def test_crashed_worker_cannot_overwrite_recovered_task(self):
        """A stale worker cannot write SUCCESS after recovery has reassigned the task."""
        _, task_id = _make_task()

        stale_worker_id = "stale-worker-42"
        _atomic_claim(stale_worker_id, task_id, lease_seconds=1.0)

        # Expire lease immediately
        with SessionLocal() as db:
            t = db.get(Task, task_id)
            t.lease_expires_at = _now_utc() - timedelta(seconds=10)
            db.commit()

        # Recovery takes over
        with SessionLocal() as db:
            recovered = recover_stale_tasks(db)
        assert task_id in recovered

        # Stale worker tries to write SUCCESS — must be rejected
        succeeded = _transition_to_success(stale_worker_id, task_id, {"late": "result"})
        assert not succeeded, "Stale worker must not overwrite recovered task"

        task = _load_task(task_id)
        assert task.status == TaskStatus.QUEUED, (
            f"Recovered task must remain QUEUED, not be overwritten to SUCCESS. "
            f"Got: {task.status}"
        )

    def test_two_recovery_workers_exactly_one_wins(self):
        """Two concurrent recovery attempts on the same stale task → exactly 1 wins."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        _, task_id = _make_task()
        _atomic_claim("stale-xyz", task_id, lease_seconds=1.0)

        with SessionLocal() as db:
            t = db.get(Task, task_id)
            t.lease_expires_at = _now_utc() - timedelta(seconds=10)
            db.commit()

        all_recovered = []

        def _recover():
            with SessionLocal() as db:
                return recover_stale_tasks(db)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_recover), pool.submit(_recover)]
            for fut in as_completed(futures):
                all_recovered.extend(fut.result())

        # task_id should appear exactly once across both results
        count = all_recovered.count(task_id)
        assert count == 1, f"Expected exactly 1 recovery, got {count}"


# ---------------------------------------------------------------------------
# Test: Timeout behavior
# ---------------------------------------------------------------------------

class TestTimeoutBehavior:
    def test_task_exceeding_timeout_fails_with_retry(self):
        """A task that sleeps longer than its timeout should be retried then fail."""
        _, task_id = _make_task(
            "sleep",
            payload={"seconds": 30.0},  # much longer than timeout
            timeout_seconds=2,
            max_retries=1,
        )
        publish_task(task_id)

        worker = _start_worker("timeout-tester")
        try:
            # Budget: (max_retries+1) * (timeout + backoff) + margin
            task = _wait_terminal(task_id, timeout=30.0)
            assert task.status in (TaskStatus.FAILED, TaskStatus.TIMED_OUT), (
                f"Expected FAILED or TIMED_OUT, got {task.status}"
            )
            assert task.attempt_count >= 1, "Should have at least one attempt"
        finally:
            _stop_worker(worker)

    def test_timeout_does_not_kill_worker(self):
        """After a timeout, the worker should continue processing other tasks."""
        # Submit a timeout task
        _, timeout_task_id = _make_task(
            "sleep", payload={"seconds": 30.0},
            timeout_seconds=2, max_retries=0,
        )
        publish_task(timeout_task_id)

        # Submit a normal task
        _, normal_task_id = _make_task(
            "sleep", payload={"seconds": 0.1},
            timeout_seconds=30, max_retries=0,
        )
        publish_task(normal_task_id)

        worker = _start_worker("timeout-continue")
        try:
            # Both tasks should reach terminal state
            _wait_terminal(timeout_task_id, timeout=15.0)
            normal_task = _wait_terminal(normal_task_id, timeout=15.0)

            # Normal task must have succeeded
            assert normal_task.status == TaskStatus.SUCCESS, (
                f"Normal task must succeed even after a timeout task. "
                f"Got: {normal_task.status}"
            )
        finally:
            _stop_worker(worker)


# ---------------------------------------------------------------------------
# Test: Retry behavior
# ---------------------------------------------------------------------------

class TestRetryBehavior:
    def test_retry_on_transient_error_eventually_succeeds(self):
        """A task that fails transiently should retry and eventually succeed."""
        from app.workers.runtime import _process_task
        from app.workers import handlers
        import threading

        _, task_id = _make_task(
            "sleep", payload={"seconds": 0.01},
            timeout_seconds=30, max_retries=3,
        )

        attempt_count = 0
        original_handler = handlers.HANDLERS["sleep"]
        lock = threading.Lock()

        def failing_then_succeeding(payload):
            nonlocal attempt_count
            with lock:
                attempt_count += 1
                if attempt_count <= 1:
                    raise RuntimeError("Transient I/O error")
            return original_handler(payload)

        handlers.HANDLERS["sleep"] = failing_then_succeeding
        try:
            # First attempt fails
            _process_task("retry-test-worker", task_id)
            task = _load_task(task_id)
            assert task.status == TaskStatus.QUEUED, f"Expected QUEUED for retry, got {task.status}"
            assert task.attempt_count == 1

            # Second attempt succeeds
            _process_task("retry-test-worker", task_id)
            task = _load_task(task_id)
            assert task.status == TaskStatus.SUCCESS, f"Expected SUCCESS, got {task.status}"
            assert task.attempt_count == 2
        finally:
            handlers.HANDLERS["sleep"] = original_handler

    def test_permanent_failure_after_max_retries_exhausted(self):
        """After all retries are exhausted, task must reach FAILED."""
        from app.workers.runtime import _process_task
        from app.workers import handlers
        from app.workers.exceptions import NonRetryableError

        _, task_id = _make_task(
            "sleep", payload={"seconds": 0.01},
            timeout_seconds=30, max_retries=0,
        )

        original_handler = handlers.HANDLERS["sleep"]

        def always_fails(payload):
            raise RuntimeError("Permanent connection refused")

        handlers.HANDLERS["sleep"] = always_fails
        try:
            _process_task("perm-fail-worker", task_id)
            task = _load_task(task_id)
            assert task.status == TaskStatus.FAILED, f"Expected FAILED, got {task.status}"
        finally:
            handlers.HANDLERS["sleep"] = original_handler

    def test_non_retryable_error_fails_immediately(self):
        """A NonRetryableError or ValueError must not trigger retries."""
        from app.workers.runtime import _process_task
        from app.workers import handlers
        from app.workers.exceptions import NonRetryableError

        _, task_id = _make_task(
            "sleep", payload={"seconds": 0.01},
            timeout_seconds=30, max_retries=5,  # would retry if retryable
        )

        original_handler = handlers.HANDLERS["sleep"]

        def non_retryable_error(payload):
            raise NonRetryableError("Invalid configuration — do not retry")

        handlers.HANDLERS["sleep"] = non_retryable_error
        try:
            _process_task("nonretry-worker", task_id)
            task = _load_task(task_id)
            assert task.status == TaskStatus.FAILED, f"Expected FAILED immediately, got {task.status}"
            assert task.attempt_count == 1, "Should have failed on first attempt without retry"
        finally:
            handlers.HANDLERS["sleep"] = original_handler


# ---------------------------------------------------------------------------
# Test: Redis outage — PostgreSQL durability
# ---------------------------------------------------------------------------

class TestRedisDurability:
    def test_task_stays_queued_in_postgres_when_redis_publish_fails(self):
        """A task committed to PostgreSQL as QUEUED must remain there even if Redis publish fails."""
        from unittest.mock import patch

        with patch("app.queue.publisher.get_redis_client") as mock_redis:
            # Simulate Redis being completely unavailable
            mock_redis.side_effect = ConnectionError("Redis unavailable")

            _, task_id = _make_task()
            # publish_task returns False but must not raise
            result = publish_task(task_id)

        # Task must still be QUEUED in PostgreSQL
        task = _load_task(task_id)
        assert task.status == TaskStatus.QUEUED, (
            f"Task must remain QUEUED in PostgreSQL even when Redis is down. "
            f"Got: {task.status}"
        )

    def test_queued_task_in_postgres_can_be_recovered_and_executed(self):
        """A task that is QUEUED in PostgreSQL (not yet in Redis) can be re-enqueued and executed."""
        _, task_id = _make_task("sleep", {"seconds": 0.05})
        # Do NOT publish to Redis — task is only in PostgreSQL

        # Manually publish (simulating reconciliation)
        published = publish_task(task_id)
        assert published, "publish_task should succeed when Redis is available"

        worker = _start_worker("durability-worker")
        try:
            task = _wait_terminal(task_id, timeout=15.0)
            assert task.status == TaskStatus.SUCCESS, (
                f"Task should execute successfully after being re-published. "
                f"Got: {task.status}"
            )
        finally:
            _stop_worker(worker)
