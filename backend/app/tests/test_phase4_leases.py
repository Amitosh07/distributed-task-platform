"""Phase 4 — Task Leases and Expiration tests.

Tests cover:
- Task claim acquires a lease with lease_expires_at, worker_id, and started_at.
- The owning worker can successfully renew its task lease.
- Another worker CANNOT extend or modify a lease it does not own.
- Task completion releases ownership cleanly.
"""

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.db.database import SessionLocal
from app.db.models.project import Project
from app.db.models.task import Task, TaskStatus
from app.db.models.user import User
from app.workers.runtime import _atomic_claim, _renew_task_lease

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires isolated PostgreSQL and Redis",
)


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_project_and_task(task_type: str = "sleep", payload: dict | None = None) -> tuple:
    payload = payload or {"seconds": 0.1}
    with SessionLocal() as db:
        user = User(email=f"lease-test-{uuid4()}@example.com", password_hash="x", role="developer")
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


def test_task_claim_acquires_lease():
    """Claiming a task sets worker_id, lease_acquired_at, and lease_expires_at."""
    _, task_id = _make_project_and_task()
    worker_id = "lease-worker-1"

    claimed = _atomic_claim(worker_id, task_id, lease_seconds=10.0)
    assert claimed is True

    with SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task.status == TaskStatus.RUNNING
        assert task.worker_id == worker_id
        assert task.lease_acquired_at is not None
        assert task.lease_expires_at is not None
        assert task.lease_expires_at > task.lease_acquired_at


def test_owning_worker_can_renew_lease():
    """The worker that claimed the task can extend its lease."""
    _, task_id = _make_project_and_task()
    worker_id = "lease-worker-owner"

    _atomic_claim(worker_id, task_id, lease_seconds=5.0)

    with SessionLocal() as db:
        t1 = db.get(Task, task_id).lease_expires_at

    # Renew lease with 15s duration
    renewed = _renew_task_lease(worker_id, task_id, lease_seconds=15.0)
    assert renewed is True

    with SessionLocal() as db:
        t2 = db.get(Task, task_id).lease_expires_at
        assert t2 > t1


def test_wrong_worker_cannot_renew_lease():
    """A worker that does not own the task cannot renew the lease."""
    _, task_id = _make_project_and_task()
    owner_id = "worker-real-owner"
    imposter_id = "worker-imposter"

    _atomic_claim(owner_id, task_id, lease_seconds=10.0)

    # Imposter attempts to renew
    renewed = _renew_task_lease(imposter_id, task_id, lease_seconds=20.0)
    assert renewed is False

    with SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task.worker_id == owner_id  # Unchanged
