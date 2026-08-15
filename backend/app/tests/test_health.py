import os

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires isolated PostgreSQL")


def test_liveness_is_independent_of_redis(client):
    """Liveness must pass regardless of Redis state."""
    assert client.get("/health/live").json() == {"status": "ok"}


def test_readiness_requires_postgresql_and_redis(client):
    """Readiness requires both PostgreSQL and Redis (Phase 2)."""
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
