"""Phase 5 tests: Workflow API Endpoints.

Tests:
- POST /v1/workflows (201 Created)
- Unauthorized project workflow creation (403 Forbidden)
- POST /v1/workflows/{id}/run (202 Accepted)
- GET /v1/workflows/{id}/runs/{run_id} (200 OK)
- Nonexistent workflow handling (404 Not Found)
"""

import os
from uuid import uuid4
import pytest
from app.tests.conftest import auth_headers, register_and_token

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires isolated PostgreSQL and Redis",
)


def _setup_user_and_project(client, email="wf_owner@example.com"):
    token = register_and_token(client, email=email)
    res = client.post(
        "/v1/projects",
        headers=auth_headers(token),
        json={"name": "Workflow Project"},
    )
    assert res.status_code == 201
    return token, res.json()["id"]


def test_create_workflow_authorized(client):
    """Workflow creation with valid nodes, edges, and failure policy."""
    token, project_id = _setup_user_and_project(client)

    payload = {
        "project_id": project_id,
        "name": "ETL Pipeline",
        "failure_policy": "FAIL_FAST",
        "nodes": [
            {"node_key": "extract", "task_type": "sleep", "payload": {"seconds": 0.01}},
            {"node_key": "transform", "task_type": "sleep", "payload": {"seconds": 0.01}},
            {"node_key": "load", "task_type": "sleep", "payload": {"seconds": 0.01}},
        ],
        "edges": [
            {"from": "extract", "to": "transform"},
            {"from": "transform", "to": "load"},
        ],
    }

    res = client.post("/v1/workflows", headers=auth_headers(token), json=payload)
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["name"] == "ETL Pipeline"
    assert data["failure_policy"] == "FAIL_FAST"
    assert len(data["nodes"]) == 3
    assert len(data["edges"]) == 2
    assert {"from_node_key": "extract", "to_node_key": "transform"} in data["edges"]
    assert {"from_node_key": "transform", "to_node_key": "load"} in data["edges"]


def test_create_workflow_unauthorized_project(client):
    """User cannot create a workflow for another user's project."""
    token1, project_id1 = _setup_user_and_project(client, "user1@example.com")
    token2, _ = _setup_user_and_project(client, "user2@example.com")

    payload = {
        "project_id": project_id1,
        "name": "Hacked Pipeline",
        "nodes": [{"node_key": "A", "task_type": "sleep", "payload": {"seconds": 0.01}}],
        "edges": [],
    }

    res = client.post("/v1/workflows", headers=auth_headers(token2), json=payload)
    assert res.status_code in (403, 404)


def test_start_workflow_run(client):
    """Triggering a workflow run returns 202 Accepted with initial run state."""
    token, project_id = _setup_user_and_project(client)

    wf_res = client.post(
        "/v1/workflows",
        headers=auth_headers(token),
        json={
            "project_id": project_id,
            "name": "Diamond Workflow",
            "failure_policy": "CONTINUE",
            "nodes": [
                {"node_key": "A", "task_type": "sleep", "payload": {"seconds": 0.01}},
                {"node_key": "B", "task_type": "sleep", "payload": {"seconds": 0.01}},
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
    assert wf_res.status_code == 201
    wf_id = wf_res.json()["id"]

    run_res = client.post(f"/v1/workflows/{wf_id}/run", headers=auth_headers(token))
    assert run_res.status_code == 202, run_res.text
    run_data = run_res.json()
    assert run_data["workflow_id"] == wf_id
    assert run_data["status"] == "RUNNING"
    assert run_data["failure_policy"] == "CONTINUE"
    assert len(run_data["nodes"]) == 4

    # Root node A should be RUNNING, others PENDING
    node_status_map = {n["node_key"]: n["status"] for n in run_data["nodes"]}
    assert node_status_map["A"] == "RUNNING"
    assert node_status_map["B"] == "PENDING"
    assert node_status_map["C"] == "PENDING"
    assert node_status_map["D"] == "PENDING"


def test_get_workflow_run(client):
    """Inspect workflow run status via GET endpoint."""
    token, project_id = _setup_user_and_project(client)

    wf_res = client.post(
        "/v1/workflows",
        headers=auth_headers(token),
        json={
            "project_id": project_id,
            "name": "Inspectable Workflow",
            "nodes": [{"node_key": "step1", "task_type": "sleep", "payload": {"seconds": 0.01}}],
            "edges": [],
        },
    )
    wf_id = wf_res.json()["id"]

    run_res = client.post(f"/v1/workflows/{wf_id}/run", headers=auth_headers(token))
    run_id = run_res.json()["id"]

    get_res = client.get(f"/v1/workflows/{wf_id}/runs/{run_id}", headers=auth_headers(token))
    assert get_res.status_code == 200
    inspect_data = get_res.json()
    assert inspect_data["id"] == run_id
    assert inspect_data["workflow_id"] == wf_id
    assert len(inspect_data["nodes"]) == 1
    assert inspect_data["nodes"][0]["node_key"] == "step1"


def test_run_nonexistent_workflow_404(client):
    """Starting run for a non-existent workflow returns 404."""
    token, _ = _setup_user_and_project(client)
    random_id = str(uuid4())
    res = client.post(f"/v1/workflows/{random_id}/run", headers=auth_headers(token))
    assert res.status_code == 404
