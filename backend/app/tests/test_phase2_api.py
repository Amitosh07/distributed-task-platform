"""Phase 2 — API integration tests for the async task pipeline.

Tests cover:
- Task submission returns QUEUED status immediately
- Task is enqueued in Redis after submission
- Submission is async: response time << task execution time
- Task eventually reaches SUCCESS after the worker processes it
- Failed task reaches FAILED after the worker processes it
- Readiness endpoint reflects PostgreSQL + Redis
- Idempotency key still works in Phase 2
"""

import os
import time
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models.task import Task, TaskStatus
from app.queue.consumer import consume_task
from app.queue.publisher import QUEUE_NAME
from app.tests.conftest import auth_headers, register_and_token

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires isolated PostgreSQL and Redis",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register_project_token(client):
    token = register_and_token(client)
    project_id = client.post(
        "/v1/projects", headers=auth_headers(token), json={"name": "API Phase 2 Tests"}
    ).json()["id"]
    return token, project_id


def _submit_task(client, token, project_id, task_type="sleep", payload=None, **kwargs):
    body = {
        "project_id": project_id,
        "type": task_type,
        "payload": payload or {"seconds": 0.01},
        "priority": "NORMAL",
        "timeout_seconds": 30,
        "max_retries": 0,
        **kwargs,
    }
    return client.post("/v1/tasks", headers=auth_headers(token), json=body)


def _run_worker_for_task(task_id_str: str, worker_id: str = "test-worker") -> None:
    """Pull the task from the queue and process it synchronously (test helper)."""
    from uuid import UUID
    from app.workers.runtime import _process_task
    _process_task(worker_id, UUID(task_id_str))



def _load_task(task_id: str) -> Task:
    from uuid import UUID
    with SessionLocal() as db:
        task = db.get(Task, UUID(task_id))
        db.expunge(task)
        return task


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPhase2Api:
    def test_task_submission_returns_queued_immediately(self, client):
        """POST /v1/tasks must return QUEUED status synchronously."""
        token, project_id = _register_project_token(client)
        response = _submit_task(client, token, project_id)

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "QUEUED"
        assert data["queued_at"] is not None
        assert data["started_at"] is None
        assert data["finished_at"] is None

    def test_task_is_enqueued_in_redis(self, client, redis_client):
        """After submission the task ID must be present on the Redis queue."""
        token, project_id = _register_project_token(client)
        response = _submit_task(client, token, project_id)
        task_id = response.json()["id"]

        # The queue should have exactly one message.
        raw = redis_client.lindex(QUEUE_NAME, 0)
        assert raw is not None
        import json
        data = json.loads(raw)
        assert data["task_id"] == task_id

    def test_submission_is_asynchronous(self, client):
        """API response time must be well below the sleep task duration."""
        token, project_id = _register_project_token(client)

        start = time.monotonic()
        response = _submit_task(client, token, project_id, payload={"seconds": 5})
        elapsed = time.monotonic() - start

        assert response.status_code == 201
        assert elapsed < 2.0, (
            f"API response took {elapsed:.2f}s — expected < 2s for a 5s sleep task; "
            "the API must not wait for task execution"
        )

    def test_task_reaches_success_after_worker_processes_it(self, client):
        """A valid task must reach SUCCESS once the worker processes it."""
        token, project_id = _register_project_token(client)
        response = _submit_task(client, token, project_id, payload={"seconds": 0.01})
        task_id = response.json()["id"]

        _run_worker_for_task(task_id)

        task = _load_task(task_id)
        assert task.status == TaskStatus.SUCCESS
        assert task.result_summary is not None
        assert task.finished_at is not None

    def test_task_reaches_failed_on_invalid_payload(self, client):
        """A task with an invalid payload must reach FAILED (not crash the worker)."""
        token, project_id = _register_project_token(client)
        response = _submit_task(client, token, project_id, payload={"seconds": -1})
        task_id = response.json()["id"]

        _run_worker_for_task(task_id)

        task = _load_task(task_id)
        assert task.status == TaskStatus.FAILED
        assert task.error_message is not None

    def test_worker_survives_failure_and_processes_next_task(self, client):
        """After a FAILED task, the next task must still reach SUCCESS."""
        token, project_id = _register_project_token(client)

        bad_resp = _submit_task(client, token, project_id, payload={"seconds": -1})
        good_resp = _submit_task(client, token, project_id, payload={"seconds": 0.01})
        bad_id = bad_resp.json()["id"]
        good_id = good_resp.json()["id"]

        _run_worker_for_task(bad_id)
        _run_worker_for_task(good_id)

        bad_task = _load_task(bad_id)
        good_task = _load_task(good_id)
        assert bad_task.status == TaskStatus.FAILED
        assert good_task.status == TaskStatus.SUCCESS

    def test_idempotency_key_returns_existing_task(self, client):
        """Submitting the same idempotency key twice returns the original task."""
        token, project_id = _register_project_token(client)
        first = _submit_task(client, token, project_id, idempotency_key="test-key-1")
        second = _submit_task(client, token, project_id, idempotency_key="test-key-1")

        assert first.status_code == 201
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]

    def test_get_task_reflects_worker_execution(self, client):
        """GET /v1/tasks/{id} must return the final state after worker runs."""
        token, project_id = _register_project_token(client)
        task_id = _submit_task(client, token, project_id, payload={"seconds": 0.01}).json()["id"]

        _run_worker_for_task(task_id)

        get_response = client.get(f"/v1/tasks/{task_id}", headers=auth_headers(token))
        assert get_response.status_code == 200
        data = get_response.json()
        assert data["status"] == "SUCCESS"
        assert data["result_summary"] is not None

    def test_readiness_requires_both_postgres_and_redis(self, client):
        """GET /health/ready must return 200 when both PostgreSQL and Redis are up."""
        response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

    def test_readiness_fails_when_redis_unavailable(self, client):
        """GET /health/ready must return 503 when Redis is unreachable."""
        with patch("app.api.routes.health.check_redis_health", return_value=False):
            response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "redis_unavailable"

    def test_liveness_is_always_ok(self, client):
        """GET /health/live must always return 200 regardless of Redis state."""
        with patch("app.queue.redis_client.check_redis_health", return_value=False):
            response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
