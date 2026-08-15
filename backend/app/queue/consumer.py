"""Redis task queue consumer.

Responsibility: blocking-pop a message from the task queue and parse the
task ID from the JSON payload.

The consumer only knows about the queue protocol (BLPOP + JSON parse).
All business logic — loading the task from PostgreSQL, state transitions,
handler dispatch — lives in the worker runtime.
"""

import json
import logging
from uuid import UUID

from app.queue.redis_client import get_redis_client
from app.queue.publisher import QUEUE_NAME

logger = logging.getLogger(__name__)


def consume_task(timeout_seconds: int = 5) -> UUID | None:
    """Block until a task ID is available on the queue, then return it.

    Returns the parsed task UUID, or None if the timeout elapsed with no
    message.  Raises on JSON / format errors so the worker can log and
    continue to the next message.

    Args:
        timeout_seconds: How long to block waiting for a message. A value
            of 0 would block forever; use a finite timeout so the worker
            loop can perform periodic housekeeping if needed.
    """
    client = get_redis_client()
    result = client.blpop(QUEUE_NAME, timeout=timeout_seconds)
    if result is None:
        # Timeout elapsed with no message — normal operation.
        return None
    _queue_name, raw_message = result
    try:
        data = json.loads(raw_message)
        task_id = UUID(data["task_id"])
        logger.debug("Consumed task ID %s from %s", task_id, QUEUE_NAME)
        return task_id
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.error("Malformed queue message discarded: %r — %s", raw_message, exc)
        raise
