"""Phase 4 — Stale Task Recovery, Race Conditions & Failure Integration tests.

Tests cover:
- Stale RUNNING task with expired lease is detected and recovered to QUEUED.
- Re-enqueued recovered task can be picked up and executed to SUCCESS.
- Concurrency race: recovery vs late task completion by original worker.
- Concurrency race: two recovery workers racing to recover the same expired task.
- Real failure integration test: Worker A claims task, Worker A dies -> lease expires -> Worker B recovers and executes task to SUCCESS.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.db.database import SessionLocal
from app.db.models.project import Project
from app.db.models.task import Task, TaskStatus
from app.db.models.user import User
from app.queue.publisher import QUEUE_NAME, publish_task
from app.queue.redis_client import get_redis_client
from app.services.recovery import recover_stale_tasks
from app.workers.runtime import _atomic_claim, _process_task, _transition_to_success

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires isolated PostgreSQL and Redis",
)


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_project_and_task(task_type: str = "sleep", payload: dict | None = None) -> tuple:
    payload = payload or {"seconds": 0.05}
    with SessionLocal() as db:
        user = User(email=f"recovery-test-{uuid4()}@example.com", password_hash="x", role="developer")
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
            timeout_seconds=30,
            max_retries=3,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return project.id, task.id


def _load_task(task_id: UUID) -> Task:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        db.expunge(task)
        return task


def _start_worker(worker_id: str) -> tuple[subprocess.Popen, object]:
    backend_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    env = {
        **os.environ,
        "TEST_DATABASE_URL": os.environ["TEST_DATABASE_URL"],
        "TEST_REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/1"),
        "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
        "JWT_SECRET_KEY": os.environ.get("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-jwt-signing"),
        "ENVIRONMENT": "test",
        "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/1"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "TASK_LEASE_SECONDS": "2.0",
        "HEARTBEAT_INTERVAL_SECONDS": "1.0",
        "RECOVERY_INTERVAL_SECONDS": "1.0",
    }
    f = tempfile.TemporaryFile(mode="w+b")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.workers.runtime", "--worker-id", worker_id],
        cwd=backend_dir,
        env=env,
        stdout=f,
        stderr=subprocess.STDOUT,
    )
    return proc, f


def _stop_worker(worker_handle: tuple[subprocess.Popen, object], timeout: float = 5.0) -> str:
    proc, log_file = worker_handle
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    log_file.seek(0)
    raw = log_file.read()
    log_file.close()
    return raw.decode("utf-8", errors="replace")


def _wait_for_terminal(task_id: UUID, timeout: float = 30.0) -> Task:
    terminal = {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.DEAD_LETTER, TaskStatus.TIMED_OUT}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = _load_task(task_id)
        if task.status in terminal:
            return task
        time.sleep(0.1)
    task = _load_task(task_id)
    raise AssertionError(
        f"Task {task_id} did not reach a terminal state within {timeout}s. "
        f"Current status: {task.status}"
    )


class TestStaleTaskRecovery:
    def test_stale_task_is_recovered_and_requeued(self, redis_client):
        """A RUNNING task with expired lease is recovered to QUEUED and re-enqueued to Redis."""
        _, task_id = _make_project_and_task()
        worker_id = "crashed-worker"

        # Worker claims task with 1-second lease
        _atomic_claim(worker_id, task_id, lease_seconds=1.0)

        # Artificially expire the lease
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            task.lease_expires_at = _now_utc() - timedelta(seconds=10)
            db.commit()

        # Run recovery
        with SessionLocal() as db:
            recovered = recover_stale_tasks(db)
            assert task_id in recovered

        task = _load_task(task_id)
        assert task.status == TaskStatus.QUEUED
        assert task.worker_id is None
        assert task.lease_acquired_at is None

        # Verify task was pushed back to Redis queue
        raw = redis_client.lpop(QUEUE_NAME)
        assert raw is not None
        data = json.loads(raw)
        assert data["task_id"] == str(task_id)

    def test_recovery_vs_late_completion_race(self):
        """If recovery already recovered an expired task, late completion by original worker is ignored."""
        _, task_id = _make_project_and_task()
        worker_id = "slow-worker"

        _atomic_claim(worker_id, task_id, lease_seconds=1.0)

        # Recovery runs after lease expires
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            task.lease_expires_at = _now_utc() - timedelta(seconds=5)
            db.commit()
            recover_stale_tasks(db)

        # Original slow worker attempts to submit SUCCESS
        succeeded = _transition_to_success(worker_id, task_id, {"message": "late result"})
        assert succeeded is False  # Rejected because worker_id was cleared during recovery

        # Task remains in recovered QUEUED state (not overwritten to SUCCESS)
        task = _load_task(task_id)
        assert task.status == TaskStatus.QUEUED

    def test_concurrent_recovery_workers_racing(self):
        """Two concurrent workers attempting to recover the same stale task -> exactly one re-enqueues."""
        _, task_id = _make_project_and_task()
        _atomic_claim("crashed-worker", task_id, lease_seconds=1.0)

        # Expire lease
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            task.lease_expires_at = _now_utc() - timedelta(seconds=5)
            db.commit()

        def run_recovery():
            with SessionLocal() as db:
                return recover_stale_tasks(db)

        results = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run_recovery), executor.submit(run_recovery)]
            for f in as_completed(futures):
                results.append(f.result())

        # Task should have been recovered exactly once across both attempts
        all_recovered = [tid for r in results for tid in r]
        assert all_recovered.count(task_id) == 1


class TestWorkerFailureIntegration:
    def test_worker_crash_and_automatic_recovery(self):
        """Simulate Worker A crash -> lease expires -> Worker B automatically recovers & completes task."""
        _, task_id = _make_project_and_task("sleep", {"seconds": 0.1})
        publish_task(task_id)

        # Start Worker A (will claim task)
        worker_a = _start_worker("failing-worker-A")

        # Wait until Worker A claims the task (status becomes RUNNING)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            task = _load_task(task_id)
            if task.status == TaskStatus.RUNNING:
                break
            time.sleep(0.05)

        # Kill Worker A immediately to simulate abrupt process crash
        _stop_worker(worker_a, timeout=1.0)

        # Artificially set lease_expires_at to past so recovery is immediate
        with SessionLocal() as db:
            t = db.get(Task, task_id)
            if t.status == TaskStatus.RUNNING:
                t.lease_expires_at = _now_utc() - timedelta(seconds=5)
                db.commit()

        # Start Worker B (will detect stale task via background maintenance, recover, and execute)
        worker_b = _start_worker("recovery-worker-B")

        try:
            # Wait for task to reach SUCCESS via Worker B
            task = _wait_for_terminal(task_id, timeout=20.0)
            assert task.status == TaskStatus.SUCCESS
            assert task.result_summary is not None
        finally:
            _stop_worker(worker_b)
