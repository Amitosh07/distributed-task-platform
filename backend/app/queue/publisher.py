"""Redis task queue publisher.

Responsibility: serialise a task ID and push it onto the task queue.

Design:
- Queue name: ``task_queue``  (a Redis list; workers BLPOP from the left end)
- Message format: JSON string ``{"task_id": "<UUID>"}``
- Only the task ID is enqueued. The full task record lives in PostgreSQL.
- Large payloads and results are NEVER stored in Redis.

Consistency note (Phase 2):
  PostgreSQL is written first (task is already QUEUED in the DB).
  If this publish call fails, the task remains durably QUEUED in PostgreSQL.
  A future reconciler (Phase 4) will re-enqueue any tasks that are QUEUED
  but not yet picked up by a worker. This is documented in ADR-007.
"""

import json
import logging
from uuid import UUID

from app.queue.redis_client import get_redis_client

logger = logging.getLogger(__name__)

QUEUE_NAME = "task_queue"


def publish_task(task_id: UUID) -> bool:
    """Enqueue *task_id* on the Redis task queue.

    Returns True on success, False on any Redis error.
    A False return should be logged by the caller as a warning; the task
    remains QUEUED in PostgreSQL and is recoverable.
    """
    message = json.dumps({"task_id": str(task_id)})
    try:
        client = get_redis_client()
        client.rpush(QUEUE_NAME, message)
        logger.info("Task %s enqueued on %s", task_id, QUEUE_NAME)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to enqueue task %s on Redis queue %s: %s — task remains QUEUED in PostgreSQL",
            task_id,
            QUEUE_NAME,
            exc,
        )
        return False
