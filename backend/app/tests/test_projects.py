import os

import pytest

from app.tests.conftest import auth_headers, register_and_token

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires isolated PostgreSQL")


def test_create_list_and_get_own_project(client):
    token = register_and_token(client)
    created = client.post("/v1/projects", headers=auth_headers(token), json={"name": "Operations"})
    assert created.status_code == 201
    project_id = created.json()["id"]
    assert client.get("/v1/projects", headers=auth_headers(token)).json()["items"][0]["id"] == project_id
    assert client.get(f"/v1/projects/{project_id}", headers=auth_headers(token)).status_code == 200


def test_user_cannot_read_another_users_project(client):
    owner = register_and_token(client, "owner@example.com")
    project_id = client.post("/v1/projects", headers=auth_headers(owner), json={"name": "Private"}).json()["id"]
    other = register_and_token(client, "other@example.com")
    assert client.get(f"/v1/projects/{project_id}", headers=auth_headers(other)).status_code == 403
