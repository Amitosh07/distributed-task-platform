"""PostgreSQL integration-test fixtures.

Set TEST_DATABASE_URL to a dedicated, disposable PostgreSQL database. Tests
never use DATABASE_URL directly, preventing accidental modification of a
developer database.
"""

import os

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL or "postgresql+psycopg://test:test@localhost:5432/distributed_task_platform_test")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-jwt-signing")
os.environ.setdefault("ENVIRONMENT", "test")

@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    if not TEST_DATABASE_URL:
        yield
        return
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield
    command.downgrade(config, "base")


@pytest.fixture(autouse=True)
def clear_database(migrated_database):
    if not TEST_DATABASE_URL:
        yield
        return
    from sqlalchemy import text
    from app.db.database import engine

    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE tasks, projects, users RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def register_and_token(client, email: str = "owner@example.com") -> str:
    response = client.post("/v1/auth/register", json={"email": email, "password": "secure-password-123"})
    assert response.status_code == 201
    response = client.post("/v1/auth/login", json={"email": email, "password": "secure-password-123"})
    assert response.status_code == 200
    return response.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
