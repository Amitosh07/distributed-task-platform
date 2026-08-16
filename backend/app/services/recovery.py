"""Stale task and worker failure recovery service.

Authoritative source of truth: PostgreSQL.
Redis is used strictly as a dispatch queue.

Consistency Guarantee:
- Recovery uses atomic conditional SQL updates to claim stale RUNNING tasks.
- If a task lease has expired (lease_expires_at < now), it is transitioned to
  QUEUED (if attempts <= max_retries) or FAILED (if attempts exhausted).
- Only the process that successfully executes the atomic update re-publishes
  the task ID to Redis.
- Concurrency-safe: multiple workers or recovery processes racing to recover
  the same task will result in exactly one successful claimant.
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models.task import Task, TaskStatus
from app.db.models.worker import Worker, WorkerStatus
from app.queue.publisher import publish_task
from app.observability.metrics import STALE_WORKERS, WORKER_RECOVERIES

logger = logging.getLogger("recovery.service")


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def recover_stale_tasks(db: Session, now: datetime | None = None) -> list[UUID]:
    """Find and recover tasks whose worker lease has expired.

    For each expired RUNNING task:
    - If attempts <= max_retries: transitions back to QUEUED and re-enqueues on Redis.
    - If attempts > max_retries: transitions to FAILED.

    Returns the list of task IDs that were successfully recovered into QUEUED state.
    """
    now = now or _now_utc()

    # Find candidates with expired leases
    stale_tasks = db.scalars(
        select(Task.id).where(
            Task.status == TaskStatus.RUNNING,
            Task.lease_expires_at.is_not(None),
            Task.lease_expires_at < now,
        )
    ).all()

    recovered_queued_ids: list[UUID] = []

    for task_id in stale_tasks:
        result = db.execute(
            text(
                """
                UPDATE tasks
                SET    status = (CASE
                         WHEN attempt_count <= max_retries THEN 'QUEUED'::task_status
                         ELSE 'FAILED'::task_status
                       END),
                       worker_id = NULL,
                       lease_acquired_at = NULL,
                       lease_expires_at = NULL,
                       error_message = CASE
                         WHEN attempt_count <= max_retries THEN 'Recovered after worker lease expiration'
                         ELSE 'Failed: worker lease expired and max retries exhausted'
                       END,
                       finished_at = CASE
                         WHEN attempt_count <= max_retries THEN NULL
                         ELSE :now
                       END
                WHERE  id = :task_id
                AND    status = 'RUNNING'
                AND    lease_expires_at < :now
                RETURNING id, status
                """
            ),
            {"task_id": str(task_id), "now": now},
        )
        row = result.fetchone()
        db.commit()

        if row is not None:
            _id, new_status = row
            if str(new_status) == "QUEUED" or new_status == TaskStatus.QUEUED:
                logger.warning("Recovered stale task %s -> re-enqueuing in Redis", task_id)
                published = publish_task(task_id)
                if not published:
                    logger.error("Failed to re-enqueue recovered task %s to Redis", task_id)
                recovered_queued_ids.append(task_id)
                WORKER_RECOVERIES.inc()
            else:
                logger.warning("Stale task %s exceeded max_retries -> marked FAILED", task_id)

    return recovered_queued_ids


def detect_stale_workers(
    db: Session,
    stale_threshold_seconds: float = 10.0,
    now: datetime | None = None,
) -> list[str]:
    """Detect and mark workers that have not heartbeated within the threshold.

    Transitions ACTIVE workers with last_heartbeat_at < (now - threshold) to STALE.
    Returns list of worker IDs marked STALE.
    """
    now = now or _now_utc()
    from datetime import timedelta
    cutoff = now - timedelta(seconds=stale_threshold_seconds)

    result = db.execute(
        text(
            """
            UPDATE workers
            SET    status = 'STALE'::worker_status
            WHERE  status = 'ACTIVE'
            AND    last_heartbeat_at < :cutoff
            RETURNING id
            """
        ),
        {"cutoff": cutoff},
    )
    db.commit()
    stale_ids = [row[0] for row in result.fetchall()]
    if stale_ids:
        STALE_WORKERS.inc(len(stale_ids))
        logger.info("Marked %d workers as STALE: %s", len(stale_ids), stale_ids)
    return stale_ids
