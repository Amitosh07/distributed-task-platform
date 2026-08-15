"""Phase 2 — Redis queue tests.

Tests cover:
- Redis connection health check
- Publisher: enqueue a task ID
- Consumer: dequeue a task ID
- Publisher gracefully handles Redis errors (mock)
- Queue isolation: test uses DB 1, never DB 0
"""

import json
import os
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.queue.consumer import consume_task
from app.queue.publisher import QUEUE_NAME, publish_task
from app.queue.redis_client import check_redis_health

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires isolated PostgreSQL (and Redis on DB 1)",
)


def test_redis_health_check_returns_true(redis_client):
    """Redis must be reachable during tests."""
    assert check_redis_health() is True


def test_publish_task_enqueues_message(redis_client):
    """publish_task pushes a valid JSON message containing the task ID."""
    task_id = uuid4()
    result = publish_task(task_id)

    assert result is True
    raw = redis_client.lindex(QUEUE_NAME, 0)
    assert raw is not None
    data = json.loads(raw)
    assert data["task_id"] == str(task_id)


def test_consume_task_returns_published_id(redis_client):
    """consume_task returns the UUID that was published."""
    task_id = uuid4()
    publish_task(task_id)

    consumed = consume_task(timeout_seconds=2)
    assert consumed == task_id


def test_consume_task_returns_none_on_timeout():
    """consume_task returns None when the queue is empty and timeout elapses."""
    # Queue was cleared by the clear_test_redis fixture.
    result = consume_task(timeout_seconds=1)
    assert result is None


def test_publish_multiple_tasks_fifo(redis_client):
    """Queue preserves FIFO order across multiple publishes."""
    ids = [uuid4() for _ in range(3)]
    for tid in ids:
        publish_task(tid)

    for expected_id in ids:
        consumed = consume_task(timeout_seconds=2)
        assert consumed == expected_id


def test_publish_returns_false_on_redis_error():
    """publish_task returns False (does not raise) when Redis is unreachable."""
    with patch("app.queue.publisher.get_redis_client") as mock_client:
        mock_client.return_value.rpush.side_effect = Exception("connection refused")
        result = publish_task(uuid4())
    assert result is False
