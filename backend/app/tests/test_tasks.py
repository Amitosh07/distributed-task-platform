import os

import pytest
from sqlalchemy import select

from app.db.models.task import Task, TaskStatus
from app.db.database import SessionLocal
from app.tests.conftest import auth_headers, register_and_token

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires isolated PostgreSQL")


def create_project(client, token: str) -> str:
    return client.post("/v1/projects", headers=auth_headers(token), json={"name": "Tasks"}).json()["id"]


def create_task(client, token: str, project_id: str, **overrides):
    payload = {"project_id": project_id, "type": "sleep", "payload": {"seconds": 1}, "priority": "NORMAL", "timeout_seconds": 300, "max_retries": 3}
    payload.update(overrides)
    return client.post("/v1/tasks", headers=auth_headers(token), json=payload)


def test_task_creation_is_persisted_and_queued(client):
    """Phase 2: submitted tasks are immediately QUEUED (not CREATED)."""
    token = register_and_token(client)
    response = create_task(client, token, create_project(client, token))
    assert response.status_code == 201
    task_id = response.json()["id"]
    assert response.json()["status"] == "QUEUED", "Phase 2: task should be QUEUED after submission"
    assert response.json()["queued_at"] is not None, "queued_at must be set"
    with SessionLocal() as db:
        task = db.scalar(select(Task).where(Task.id == task_id))
        assert task is not None and task.status is TaskStatus.QUEUED


def test_task_retrieval_missing_and_authorization(client):
    owner = register_and_token(client)
    task_id = create_task(client, owner, create_project(client, owner)).json()["id"]
    assert client.get(f"/v1/tasks/{task_id}", headers=auth_headers(owner)).status_code == 200
    assert client.get("/v1/tasks/00000000-0000-0000-0000-000000000000", headers=auth_headers(owner)).status_code == 404
    other = register_and_token(client, "other@example.com")
    assert client.get(f"/v1/tasks/{task_id}", headers=auth_headers(other)).status_code == 403


def test_task_pagination_and_filters(client):
    token = register_and_token(client)
    project_id = create_project(client, token)
    create_task(client, token, project_id, priority="HIGH", type="http_check")
    create_task(client, token, project_id, priority="LOW", type="sleep")
    assert client.get("/v1/tasks?page=1&page_size=1", headers=auth_headers(token)).json()["total"] == 2
    assert len(client.get("/v1/tasks?status=QUEUED", headers=auth_headers(token)).json()["items"]) == 2
    assert client.get("/v1/tasks?priority=HIGH", headers=auth_headers(token)).json()["items"][0]["priority"] == "HIGH"
    assert client.get("/v1/tasks?type=http_check", headers=auth_headers(token)).json()["items"][0]["type"] == "http_check"


def test_task_cancellation(client):
    token = register_and_token(client)
    project_id = create_project(client, token)
    res = create_task(client, token, project_id)
    task_id = res.json()["id"]
    assert res.json()["status"] == "QUEUED"

    # Cancel the QUEUED task
    cancel_res = client.post(f"/v1/tasks/{task_id}/cancel", headers=auth_headers(token))
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "CANCELLED"
    assert cancel_res.json()["finished_at"] is not None

    # Trying to cancel an already CANCELLED task should be rejected (409 Conflict)
    cancel_again = client.post(f"/v1/tasks/{task_id}/cancel", headers=auth_headers(token))
    assert cancel_again.status_code == 409
    assert cancel_again.json()["error"]["code"] == "invalid_state_transition"
