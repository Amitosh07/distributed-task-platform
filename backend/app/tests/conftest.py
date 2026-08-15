"""PostgreSQL and Redis integration-test fixtures.

PostgreSQL:
    Set TEST_DATABASE_URL to a dedicated, disposable PostgreSQL database.
    Tests never use DATABASE_URL directly, preventing accidental modification
    of the developer database (workflow_platform).

Redis:
    Set TEST_REDIS_URL to a dedicated Redis database (e.g. redis://localhost:6379/1).
    Tests use DB 1 only; they NEVER flush or modify Redis DB 0.
    Only the specific queue key used by tests is cleaned up.
"""

import os

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/1")

os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL or "postgresql+psycopg://test:test@localhost:5432/workflow_platform_test")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-jwt-signing")
os.environ.setdefault("ENVIRONMENT", "test")
# Override Redis URL for all tests to use the isolated test database.
os.environ["REDIS_URL"] = TEST_REDIS_URL


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
        connection.execute(text("TRUNCATE TABLE tasks, projects, users, workers RESTART IDENTITY CASCADE"))
    yield



@pytest.fixture(autouse=True)
def clear_test_redis():
    """Delete only the test task queue key before each test.

    Never flushes the whole Redis server or touches DB 0.
    """
    from app.queue.publisher import QUEUE_NAME
    from app.queue.redis_client import get_redis_client, _pool

    # Reset the pool so it picks up the overridden REDIS_URL for this process.
    import app.queue.redis_client as _rc
    _rc._pool = None

    client = get_redis_client()
    client.delete(QUEUE_NAME)
    yield
    client.delete(QUEUE_NAME)


@pytest.fixture
def redis_client():
    """Return a Redis client connected to the test Redis DB (DB 1)."""
    from app.queue.redis_client import get_redis_client
    return get_redis_client()


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
