import pytest

from app.tests.conftest import auth_headers, register_and_token

pytestmark = pytest.mark.skipif(not __import__("os").getenv("TEST_DATABASE_URL"), reason="requires isolated PostgreSQL")


def test_register_login_and_me(client):
    token = register_and_token(client)
    response = client.get("/v1/auth/me", headers=auth_headers(token))
    assert response.status_code == 200
    assert response.json()["email"] == "owner@example.com"


def test_duplicate_email_and_wrong_password_are_rejected(client):
    register_and_token(client)
    duplicate = client.post("/v1/auth/register", json={"email": "owner@example.com", "password": "secure-password-123"})
    assert duplicate.status_code == 409
    wrong_password = client.post("/v1/auth/login", json={"email": "owner@example.com", "password": "not-the-right-password"})
    assert wrong_password.status_code == 401


def test_protected_endpoint_requires_authentication(client):
    assert client.get("/v1/auth/me").status_code == 401
