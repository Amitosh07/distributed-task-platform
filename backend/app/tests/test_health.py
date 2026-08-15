import os

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires isolated PostgreSQL")


def test_liveness_and_readiness(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}
