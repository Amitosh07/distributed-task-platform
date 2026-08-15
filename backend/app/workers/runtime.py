"""Worker runtime — Phase 2 single-worker implementation.

Start command (from backend/ directory with venv activated):
    python -m app.workers.runtime

Design:
    - One process, one loop: BLPOP → load → execute → persist.
    - Task state is always loaded fresh from PostgreSQL before execution.
    - State transitions are persisted to PostgreSQL; Redis holds only queue refs.
    - A failed task does NOT kill the worker; the loop continues.
    - No retries in Phase 2; failed tasks move to FAILED and stay there.
    - No heartbeats, leases, or recovery in Phase 2 (Phase 3/4 scope).

Lifecycle per task:
    1. BLPOP task_id from Redis queue
    2. Load Task from PostgreSQL
    3. Validate task is still QUEUED (defensive: skip if terminal/already RUNNING)
    4. Transition QUEUED → RUNNING  (set started_at, increment attempt_count)
    5. Dispatch to registered handler
    6a. Success → set result_summary, transition RUNNING → SUCCESS, set finished_at
    6b. Failure → set error_message, transition RUNNING → FAILED, set finished_at
    7. Commit to PostgreSQL
    8. Loop to step 1
"""

import logging
import signal
import sys
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.models.task import Task, TaskStatus, is_valid_transition
from app.queue.consumer import consume_task
from app.queue.redis_client import check_redis_health
from app.workers.handlers import HANDLERS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("worker.runtime")

_running = True


def _handle_sigterm(signum: int, frame: object) -> None:  # noqa: ARG001
    global _running
    logger.info("Received signal %d — worker will stop after the current task", signum)
    _running = False


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


# ---------------------------------------------------------------------------
# Task execution helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _load_task(db: Session, task_id: UUID) -> Task | None:
    """Load the task from PostgreSQL. Return None if not found."""
    task = db.get(Task, task_id)
    if task is None:
        logger.error("Task %s not found in PostgreSQL — discarding queue message", task_id)
    return task


def _transition_to_running(db: Session, task: Task) -> bool:
    """Atomically move the task from QUEUED to RUNNING.

    Returns False if the transition is not valid (defensive check).
    """
    if not is_valid_transition(task.status, TaskStatus.RUNNING):
        logger.warning(
            "Task %s is in status %s — expected QUEUED. Skipping to prevent duplicate execution.",
            task.id,
            task.status,
        )
        return False

    task.status = TaskStatus.RUNNING
    task.started_at = _now_utc()
    task.attempt_count = (task.attempt_count or 0) + 1
    db.commit()
    db.refresh(task)
    logger.info("Task %s [%s] transitioned to RUNNING (attempt %d)", task.id, task.type, task.attempt_count)
    return True


def _transition_to_success(db: Session, task: Task, result: dict) -> None:
    task.status = TaskStatus.SUCCESS
    task.finished_at = _now_utc()
    task.result_summary = result
    db.commit()
    logger.info("Task %s [%s] → SUCCESS", task.id, task.type)


def _transition_to_failed(db: Session, task: Task, error: str) -> None:
    task.status = TaskStatus.FAILED
    task.finished_at = _now_utc()
    task.error_message = error
    db.commit()
    logger.error("Task %s [%s] → FAILED: %s", task.id, task.type, error)


# ---------------------------------------------------------------------------
# Per-task processing
# ---------------------------------------------------------------------------

def _process_task(task_id: UUID) -> None:
    """Load, validate, execute, and persist one task.

    All exceptions from handler execution are caught here so that the
    outer worker loop can safely continue to the next task.
    """
    with SessionLocal() as db:
        task = _load_task(db, task_id)
        if task is None:
            return

        # Defensive duplicate / terminal-state check.
        if task.status != TaskStatus.QUEUED:
            logger.warning(
                "Task %s has status %s (not QUEUED) — skipping. "
                "This can happen if a task was already processed or cancelled.",
                task.id,
                task.status,
            )
            return

        if not _transition_to_running(db, task):
            return

        # Resolve handler — unknown type is a hard failure.
        handler = HANDLERS.get(task.type)
        if handler is None:
            _transition_to_failed(
                db,
                task,
                f"Unknown task type '{task.type}'. Not in registered handler list.",
            )
            return

        # Execute handler — catch ALL exceptions so the worker loop continues.
        try:
            logger.info("Task %s [%s] executing handler…", task.id, task.type)
            result = handler(task.payload)
            _transition_to_success(db, task, result)
        except Exception as exc:  # noqa: BLE001
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.exception("Task %s [%s] handler raised an exception", task.id, task.type)
            _transition_to_failed(db, task, error_msg)


# ---------------------------------------------------------------------------
# Main worker loop
# ---------------------------------------------------------------------------

def run() -> None:
    """Start the worker and block until signalled to stop."""
    logger.info("=" * 60)
    logger.info("Worker starting — Phase 2 single-worker")
    logger.info("=" * 60)

    # Verify Redis is reachable before entering the loop.
    if not check_redis_health():
        logger.critical("Cannot connect to Redis on startup — aborting")
        sys.exit(1)
    logger.info("Redis connection verified")

    # Verify PostgreSQL is reachable.
    try:
        with SessionLocal() as db:
            db.execute(__import__("sqlalchemy").text("SELECT 1"))
        logger.info("PostgreSQL connection verified")
    except Exception as exc:
        logger.critical("Cannot connect to PostgreSQL on startup: %s — aborting", exc)
        sys.exit(1)

    logger.info("Worker is ready — waiting for tasks on 'task_queue'")

    while _running:
        try:
            task_id = consume_task(timeout_seconds=5)
        except Exception as exc:  # noqa: BLE001
            # Malformed message or transient Redis error — log and keep looping.
            logger.error("Error consuming from queue: %s — continuing", exc)
            continue

        if task_id is None:
            # Timeout — no task available, continue polling.
            continue

        logger.info("Worker received task %s", task_id)
        try:
            _process_task(task_id)
        except Exception as exc:  # noqa: BLE001
            # Outer safety net: _process_task should never raise, but if it
            # does (e.g. DB connection failure) we log and keep the worker alive.
            logger.error("Unexpected error processing task %s: %s — worker continues", task_id, exc)

    logger.info("Worker stopped gracefully")


if __name__ == "__main__":
    run()
