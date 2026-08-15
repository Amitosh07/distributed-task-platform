"""Worker runtime — Phase 3: multiple workers, atomic task claim.

Start command (from backend/ directory with venv activated):

    python -m app.workers.runtime --worker-id worker-1
    python -m app.workers.runtime --worker-id worker-2

Or via environment variable:

    WORKER_ID=worker-1 python -m app.workers.runtime

Worker ID defaults to a hostname-based value if not supplied.

Phase 3 design:
    - Multiple independent worker processes can run simultaneously.
    - All workers consume from the SAME Redis queue (task_queue).
    - BLPOP naturally removes a message so only one worker receives each message.
    - The QUEUED → RUNNING transition is made atomically via a PostgreSQL
      conditional UPDATE (WHERE status = 'QUEUED').  This prevents two workers
      from executing the same task even if both receive the same queue message
      (e.g., through Redis retry/re-delivery).
    - Only the worker whose UPDATE affects exactly one row is the successful
      claimant; the other skips execution.
    - The claim transaction is committed before the handler runs, so the DB
      connection is not held open for the full task duration.
    - No heartbeats, leases, or recovery in Phase 3 (Phase 4 scope).

Known Phase 3 limitation:
    If a worker crashes after claiming a task (status=RUNNING), the task
    stays RUNNING indefinitely.  There is no lease or heartbeat-based
    recovery yet.  This is documented and expected for Phase 3.

Lifecycle per task:
    1. BLPOP task_id from Redis queue
    2. Attempt atomic claim:
         UPDATE tasks SET status='RUNNING', started_at=now,
                          attempt_count=attempt_count+1
         WHERE id=:task_id AND status='QUEUED'
         RETURNING id
    3. If rowcount == 0: another worker already claimed it — skip.
    4. Commit claim.
    5. Reload task from PostgreSQL to get full row.
    6. Dispatch to registered handler.
    7. On success  → RUNNING → SUCCESS  (commit)
    8. On failure  → RUNNING → FAILED   (commit)
    9. Loop.
"""

import argparse
import logging
import os
import signal
import socket
import sys
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text

from app.db.database import SessionLocal
from app.db.models.task import Task, TaskStatus
from app.queue.consumer import consume_task
from app.queue.redis_client import check_redis_health
from app.workers.handlers import HANDLERS

# ---------------------------------------------------------------------------
# Logging setup — format includes worker_id as a field for easy grepping
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("worker.runtime")

_running = True


def _handle_sigterm(signum: int, frame: object) -> None:  # noqa: ARG001
    global _running
    _log(None, None, "shutdown_signal", f"Received signal {signum} — stopping after current task")
    _running = False


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


# ---------------------------------------------------------------------------
# Structured logging helper
# ---------------------------------------------------------------------------

_worker_id: str = "unknown"


def _log(worker_id: str | None, task_id, event: str, detail: str = "") -> None:
    """Emit a structured log line that is easy to grep by worker_id / task_id."""
    wid = worker_id or _worker_id
    parts = [f"worker_id={wid}"]
    if task_id is not None:
        parts.append(f"task_id={task_id}")
    parts.append(f"event={event}")
    if detail:
        parts.append(detail)
    logger.info(" ".join(parts))


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _default_worker_id() -> str:
    """Generate a default worker ID from hostname + PID."""
    return f"{socket.gethostname()}-{os.getpid()}"


# ---------------------------------------------------------------------------
# Atomic task claim (Phase 3 key change)
# ---------------------------------------------------------------------------

def _atomic_claim(worker_id: str, task_id: UUID) -> bool:
    """Atomically transition the task from QUEUED → RUNNING.

    Uses a conditional UPDATE (WHERE status = 'QUEUED') so that two workers
    racing for the same task ID will result in exactly ONE successful claim.

    The claim is committed before the handler runs, releasing the DB
    connection lock promptly.

    Returns True if this worker claimed the task, False if another worker
    already did (or the task is in a non-QUEUED state).
    """
    now = _now_utc()
    with SessionLocal() as db:
        result = db.execute(
            text(
                """
                UPDATE tasks
                SET    status       = 'RUNNING',
                       started_at   = :now,
                       attempt_count = attempt_count + 1
                WHERE  id     = :task_id
                AND    status = 'QUEUED'
                """
            ),
            {"task_id": str(task_id), "now": now},
        )
        db.commit()
        claimed = result.rowcount == 1

    if claimed:
        _log(worker_id, task_id, "task_claimed")
    else:
        _log(worker_id, task_id, "task_claim_lost",
             "task was already claimed by another worker or is not QUEUED")
    return claimed


# ---------------------------------------------------------------------------
# Result persistence helpers
# ---------------------------------------------------------------------------

def _transition_to_success(worker_id: str, task_id: UUID, result: dict) -> None:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is not None:
            task.status = TaskStatus.SUCCESS
            task.finished_at = _now_utc()
            task.result_summary = result
            db.commit()
    _log(worker_id, task_id, "task_succeeded")


def _transition_to_failed(worker_id: str, task_id: UUID, error: str) -> None:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is not None:
            task.status = TaskStatus.FAILED
            task.finished_at = _now_utc()
            task.error_message = error
            db.commit()
    _log(worker_id, task_id, "task_failed", f"error={error[:120]!r}")



# ---------------------------------------------------------------------------
# Per-task processing
# ---------------------------------------------------------------------------

def _load_task_type_and_payload(task_id: UUID) -> tuple[str, dict] | None:
    """Reload the task from PostgreSQL to get the handler inputs."""
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None:
            return None
        # Detach from session — we only need type and payload.
        return task.type, dict(task.payload)


def _process_task(worker_id: str, task_id: UUID) -> None:
    """Claim, execute, and persist results for one task.

    All handler exceptions are caught so the worker loop never dies.
    """
    _log(worker_id, task_id, "task_received")

    # ---- Phase 3: Atomic claim ----
    _log(worker_id, task_id, "task_claim_attempted")
    if not _atomic_claim(worker_id, task_id):
        # Another worker won the race — nothing to do.
        return

    # ---- Reload task inputs ----
    row = _load_task_type_and_payload(task_id)
    if row is None:
        _log(worker_id, task_id, "task_load_failed", "task disappeared after claim")
        return
    task_type, payload = row

    _log(worker_id, task_id, "task_started", f"type={task_type}")

    # ---- Resolve handler ----
    handler = HANDLERS.get(task_type)
    if handler is None:
        error = f"Unknown task type '{task_type}'. Not in registered handler list."
        _log(worker_id, task_id, "task_failed", f"error={error!r}")
        _transition_to_failed(worker_id, task_id, error)
        return

    # ---- Execute handler ----
    try:
        result = handler(payload)
        _transition_to_success(worker_id, task_id, result)
    except Exception as exc:  # noqa: BLE001
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.exception(
            "worker_id=%s task_id=%s event=handler_exception error=%r",
            worker_id, task_id, error_msg,
        )
        _transition_to_failed(worker_id, task_id, error_msg)


# ---------------------------------------------------------------------------
# Main worker loop
# ---------------------------------------------------------------------------

def run(worker_id: str) -> None:
    """Start the worker loop for the given worker_id."""
    global _worker_id
    _worker_id = worker_id

    _log(worker_id, None, "worker_starting", "=" * 50)
    _log(worker_id, None, "worker_starting", f"Phase 3 multi-worker — id={worker_id}")
    _log(worker_id, None, "worker_starting", "=" * 50)

    if not check_redis_health():
        _log(worker_id, None, "startup_failed", "Cannot connect to Redis — aborting")
        sys.exit(1)
    _log(worker_id, None, "redis_connected")

    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        _log(worker_id, None, "postgres_connected")
    except Exception as exc:
        _log(worker_id, None, "startup_failed", f"Cannot connect to PostgreSQL: {exc} — aborting")
        sys.exit(1)

    _log(worker_id, None, "worker_ready", "waiting for tasks on 'task_queue'")

    while _running:
        try:
            task_id = consume_task(timeout_seconds=5)
        except Exception as exc:  # noqa: BLE001
            logger.error("worker_id=%s event=consume_error error=%r — continuing", worker_id, str(exc))
            continue

        if task_id is None:
            continue  # Timeout — no task, keep polling.

        try:
            _process_task(worker_id, task_id)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "worker_id=%s task_id=%s event=unexpected_error error=%r — worker continues",
                worker_id, task_id, str(exc),
            )

    _log(worker_id, None, "worker_stopped", "graceful shutdown complete")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Distributed Task Platform — Worker Process",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m app.workers.runtime --worker-id worker-1
  python -m app.workers.runtime --worker-id worker-2
  WORKER_ID=worker-3 python -m app.workers.runtime
        """,
    )
    parser.add_argument(
        "--worker-id",
        default=os.environ.get("WORKER_ID") or _default_worker_id(),
        help=(
            "Unique worker identifier (default: WORKER_ID env var, "
            "or hostname-PID if not set)."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(args.worker_id)
