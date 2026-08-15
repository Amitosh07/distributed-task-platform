"""Phase 4 — Worker Heartbeats and Registry tests.

Tests cover:
- Worker registration creates an ACTIVE record in PostgreSQL.
- Worker heartbeat updates last_heartbeat_at.
- Stale worker detection marks inactive workers as STALE.
- Clean shutdown marks worker as STOPPED.
- GET /v1/workers returns registered workers and their statuses.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models.worker import Worker, WorkerStatus
from app.services.recovery import detect_stale_workers
from app.tests.conftest import auth_headers, register_and_token
from app.workers.runtime import _register_worker, _send_heartbeat, _unregister_worker

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires isolated PostgreSQL and Redis",
)


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def test_worker_registration_and_heartbeat():
    """Registering a worker creates an ACTIVE record; heartbeat updates timestamp."""
    worker_id = "test-heartbeat-worker-1"
    _register_worker(worker_id)

    with SessionLocal() as db:
        w = db.get(Worker, worker_id)
        assert w is not None
        assert w.status == WorkerStatus.ACTIVE
        t1 = w.last_heartbeat_at

    # Send heartbeat
    _send_heartbeat(worker_id)

    with SessionLocal() as db:
        w = db.get(Worker, worker_id)
        assert w is not None
        assert w.last_heartbeat_at >= t1


def test_stale_worker_detection():
    """Worker without heartbeats beyond threshold is marked STALE."""
    worker_id = "test-stale-worker-1"
    _register_worker(worker_id)

    # Artificially age the heartbeat
    past_time = _now_utc() - timedelta(seconds=20)
    with SessionLocal() as db:
        w = db.get(Worker, worker_id)
        w.last_heartbeat_at = past_time
        db.commit()

        # Run detection with 10s threshold
        stale_ids = detect_stale_workers(db, stale_threshold_seconds=10.0)
        assert worker_id in stale_ids

        db.refresh(w)
        assert w.status == WorkerStatus.STALE



def test_worker_clean_shutdown():
    """Unregistering a worker marks it STOPPED with a stopped_at timestamp."""
    worker_id = "test-stopped-worker-1"
    _register_worker(worker_id)
    _unregister_worker(worker_id)

    with SessionLocal() as db:
        w = db.get(Worker, worker_id)
        assert w is not None
        assert w.status == WorkerStatus.STOPPED
        assert w.stopped_at is not None


def test_get_workers_api_endpoint(client):
    """GET /v1/workers lists all registered workers."""
    token = register_and_token(client, email="worker-viewer@example.com")
    _register_worker("api-worker-1")
    _register_worker("api-worker-2")

    response = client.get("/v1/workers", headers=auth_headers(token))
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 2
    worker_ids = [item["id"] for item in data["items"]]
    assert "api-worker-1" in worker_ids
    assert "api-worker-2" in worker_ids
