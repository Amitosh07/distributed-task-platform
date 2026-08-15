"""Worker runtime — Phase 4: Worker Heartbeats, Task Leases, Timeouts, Retries & Failure Recovery.

Start command:
    python -m app.workers.runtime --worker-id worker-1
    python -m app.workers.runtime --worker-id worker-2

Design:
    - Multiple independent worker processes consume from the shared Redis queue (task_queue).
    - Workers register themselves in PostgreSQL as ACTIVE and maintain periodic heartbeats.
    - Claimed tasks receive an expiring lease (lease_expires_at = now + lease_duration).
    - An independent background thread renews the task lease while the task executes.
    - Tasks are executed with timeout enforcement; timeouts raise TaskTimeoutError without killing the worker.
    - Retry policy: non-retryable errors fail immediately; retryable errors (including timeouts) retry up to max_retries.
    - Concurrency-safe recovery: stale tasks (RUNNING with expired leases) are recovered by active workers and re-enqueued.
    - At-least-once execution semantics with lease and ownership protection.
"""

import argparse
import concurrent.futures
import logging
import os
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.database import SessionLocal
from app.db.models.task import Task, TaskStatus
from app.db.models.worker import Worker, WorkerStatus
from app.queue.consumer import consume_task
from app.queue.publisher import publish_task
from app.queue.redis_client import check_redis_health
from app.services.recovery import detect_stale_workers, recover_stale_tasks
from app.services.retry_policy import calculate_backoff_delay, is_retryable_error
from app.workers.exceptions import NonRetryableError, TaskTimeoutError
from app.workers.handlers import HANDLERS

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("worker.runtime")

_running = True
_worker_id: str = "unknown"
_current_task_id: UUID | None = None
_current_task_lock = threading.Lock()


def _handle_sigterm(signum: int, frame: object) -> None:  # noqa: ARG001
    global _running
    _log(None, None, "shutdown_signal", f"Received signal {signum} — stopping worker gracefully")
    _running = False


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


def _log(worker_id: str | None, task_id, event: str, detail: str = "") -> None:
    wid = worker_id or _worker_id
    parts = [f"worker_id={wid}"]
    if task_id is not None:
        parts.append(f"task_id={task_id}")
    parts.append(f"event={event}")
    if detail:
        parts.append(detail)
    logger.info(" ".join(parts))


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


# ---------------------------------------------------------------------------
# Worker registration and heartbeats
# ---------------------------------------------------------------------------

def _register_worker(worker_id: str) -> None:
    """Register or activate the worker in PostgreSQL."""
    now = _now_utc()
    hostname = socket.gethostname()
    with SessionLocal() as db:
        db.execute(
            text(
                """
                INSERT INTO workers (id, hostname, status, started_at, last_heartbeat_at)
                VALUES (:id, :hostname, 'ACTIVE', :now, :now)
                ON CONFLICT (id) DO UPDATE
                SET status = 'ACTIVE',
                    hostname = :hostname,
                    last_heartbeat_at = :now,
                    stopped_at = NULL
                """
            ),
            {"id": worker_id, "hostname": hostname, "now": now},
        )
        db.commit()
    _log(worker_id, None, "worker_registered", f"hostname={hostname}")


def _send_heartbeat(worker_id: str) -> None:
    """Update the worker's heartbeat in PostgreSQL."""
    now = _now_utc()
    with SessionLocal() as db:
        db.execute(
            text(
                """
                UPDATE workers
                SET    last_heartbeat_at = :now,
                       status = 'ACTIVE'
                WHERE  id = :id
                """
            ),
            {"id": worker_id, "now": now},
        )
        db.commit()
    _log(worker_id, None, "worker_heartbeat")


def _unregister_worker(worker_id: str) -> None:
    """Mark the worker STOPPED in PostgreSQL upon clean exit."""
    now = _now_utc()
    with SessionLocal() as db:
        db.execute(
            text(
                """
                UPDATE workers
                SET    status = 'STOPPED',
                       stopped_at = :now
                WHERE  id = :id
                """
            ),
            {"id": worker_id, "now": now},
        )
        db.commit()
    _log(worker_id, None, "worker_stopped", "marked STOPPED in registry")


def _renew_task_lease(worker_id: str, task_id: UUID, lease_seconds: float) -> bool:
    """Renew the lease for an actively executing task."""
    now = _now_utc()
    new_expiry = now + timedelta(seconds=lease_seconds)
    with SessionLocal() as db:
        result = db.execute(
            text(
                """
                UPDATE tasks
                SET    lease_expires_at = :new_expiry,
                       last_heartbeat_at = :now
                WHERE  id = :task_id
                AND    status = 'RUNNING'
                AND    worker_id = :worker_id
                """
            ),
            {"task_id": str(task_id), "worker_id": worker_id, "new_expiry": new_expiry, "now": now},
        )
        db.commit()
        renewed = result.rowcount == 1

    if renewed:
        _log(worker_id, task_id, "task_lease_renewed", f"new_expiry={new_expiry.isoformat()}")
    else:
        _log(worker_id, task_id, "task_lease_renew_failed", "task no longer owned by this worker")
    return renewed


# ---------------------------------------------------------------------------
# Background maintenance thread
# ---------------------------------------------------------------------------

class WorkerMaintenanceThread(threading.Thread):
    """Background thread for worker heartbeats, task lease renewals, and stale recovery."""

    def __init__(self, worker_id: str):
        super().__init__(daemon=True, name=f"maintenance-{worker_id}")
        self.worker_id = worker_id
        self._stop_event = threading.Event()
        self.settings = get_settings()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        last_recovery_check = 0.0
        while not self._stop_event.is_set():
            try:
                # 1. Send worker heartbeat
                _send_heartbeat(self.worker_id)

                # 2. Renew active task lease if a task is running
                with _current_task_lock:
                    active_tid = _current_task_id
                if active_tid is not None:
                    _renew_task_lease(self.worker_id, active_tid, self.settings.task_lease_seconds)

                # 3. Periodic stale task and worker recovery check
                now_mono = time.monotonic()
                if now_mono - last_recovery_check >= self.settings.recovery_interval_seconds:
                    with SessionLocal() as db:
                        recovered = recover_stale_tasks(db)
                        if recovered:
                            _log(self.worker_id, None, "stale_tasks_recovered", f"count={len(recovered)}")
                        stale_workers = detect_stale_workers(db, self.settings.worker_stale_threshold_seconds)
                        if stale_workers:
                            _log(self.worker_id, None, "stale_workers_detected", f"count={len(stale_workers)}")
                    last_recovery_check = now_mono

            except Exception as exc:  # noqa: BLE001
                logger.error("worker_id=%s event=maintenance_error error=%r", self.worker_id, str(exc))

            self._stop_event.wait(timeout=self.settings.heartbeat_interval_seconds)


# ---------------------------------------------------------------------------
# Atomic task claim with lease
# ---------------------------------------------------------------------------

def _atomic_claim(worker_id: str, task_id: UUID, lease_seconds: float | None = None) -> bool:
    """Atomically claim a task with an initial lease.

    Transition: QUEUED -> RUNNING.
    Sets worker_id, lease_acquired_at, lease_expires_at, last_heartbeat_at, and attempt_count += 1.
    """
    settings = get_settings()
    lease_duration = lease_seconds or settings.task_lease_seconds
    now = _now_utc()
    lease_expires = now + timedelta(seconds=lease_duration)

    with SessionLocal() as db:
        result = db.execute(
            text(
                """
                UPDATE tasks
                SET    status            = 'RUNNING',
                       worker_id         = :worker_id,
                       started_at        = :now,
                       lease_acquired_at = :now,
                       lease_expires_at  = :lease_expires,
                       last_heartbeat_at = :now,
                       attempt_count     = attempt_count + 1
                WHERE  id     = :task_id
                AND    status = 'QUEUED'
                """
            ),
            {
                "task_id": str(task_id),
                "worker_id": worker_id,
                "now": now,
                "lease_expires": lease_expires,
            },
        )
        db.commit()
        claimed = result.rowcount == 1

    if claimed:
        _log(worker_id, task_id, "task_claimed", f"lease_expires={lease_expires.isoformat()}")
    else:
        _log(worker_id, task_id, "task_claim_lost", "already claimed or not QUEUED")
    return claimed


# ---------------------------------------------------------------------------
# Result persistence & retry transitions
# ---------------------------------------------------------------------------

def _transition_to_success(worker_id: str, task_id: UUID, result: dict) -> bool:
    """Transition RUNNING -> SUCCESS.

    Only succeeds if the current worker still holds the task claim.
    """
    now = _now_utc()
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None or task.status != TaskStatus.RUNNING or task.worker_id != worker_id:
            _log(worker_id, task_id, "task_completion_stale", "task was recovered or expired before completion")
            return False
        task.status = TaskStatus.SUCCESS
        task.finished_at = now
        task.result_summary = result
        db.commit()

    _log(worker_id, task_id, "task_succeeded")
    return True



def _handle_task_failure(worker_id: str, task_id: UUID, exc: Exception) -> None:
    """Handle task execution failure with retry policy and backoff.

    - If retryable and attempts remain (attempt_count <= max_retries):
      re-enqueues task to QUEUED with backoff delay.
    - If non-retryable or attempts exhausted:
      transitions task to FAILED.
    """
    settings = get_settings()
    now = _now_utc()
    error_msg = f"{type(exc).__name__}: {exc}"

    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None or task.status != TaskStatus.RUNNING or task.worker_id != worker_id:
            _log(worker_id, task_id, "failure_stale", "task ownership lost prior to failure handling")
            return

        attempt_count = task.attempt_count
        max_retries = task.max_retries
        retryable = is_retryable_error(exc)
        can_retry = retryable and (attempt_count <= max_retries)

        if can_retry:
            backoff_delay = calculate_backoff_delay(
                attempt_count,
                base_seconds=settings.retry_backoff_base_seconds,
                max_seconds=settings.retry_backoff_max_seconds,
            )
            # Requeue task
            task.status = TaskStatus.QUEUED
            task.worker_id = None
            task.lease_acquired_at = None
            task.lease_expires_at = None
            task.last_heartbeat_at = None
            task.error_message = f"Attempt {attempt_count} failed ({error_msg}) — scheduled retry"
            db.commit()
            _log(
                worker_id, task_id, "task_retry_scheduled",
                f"attempt={attempt_count}/{max_retries} backoff={backoff_delay:.1f}s error={error_msg[:80]!r}"
            )
            # Re-publish to Redis queue (with backoff delay sleep if short)
            if backoff_delay > 0 and backoff_delay <= 1.0:
                time.sleep(backoff_delay)
            publish_task(task_id)
        else:
            # Final failure
            task.status = TaskStatus.FAILED
            task.finished_at = now
            task.error_message = f"Execution failed: {error_msg}"
            db.commit()
            _log(
                worker_id, task_id, "task_failed",
                f"attempt={attempt_count}/{max_retries} retryable={retryable} error={error_msg[:100]!r}"
            )


# ---------------------------------------------------------------------------
# Per-task processing with timeout enforcement
# ---------------------------------------------------------------------------

def _load_task_metadata(task_id: UUID) -> tuple[str, dict, int] | None:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None:
            return None
        return task.type, dict(task.payload), task.timeout_seconds


def _try_advance_workflow(task_id: UUID) -> None:
    """If task belongs to a workflow run node, advance the workflow engine state."""
    try:
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task is None or task.workflow_run_node_id is None:
                return
            if task.status not in (TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.DEAD_LETTER, TaskStatus.TIMED_OUT):
                return
            from app.services.workflow_engine import advance_workflow_after_task
            advance_workflow_after_task(db, task.workflow_run_node_id, task.status)
    except Exception:
        logger.exception("worker_id=%s task_id=%s event=workflow_advance_error", _worker_id, task_id)


def _process_task(worker_id: str, task_id: UUID) -> None:
    """Claim, execute with timeout, and persist results or handle retries."""
    global _current_task_id
    _log(worker_id, task_id, "task_received")

    # 1. Atomic claim with lease
    if not _atomic_claim(worker_id, task_id):
        return

    with _current_task_lock:
        _current_task_id = task_id

    try:
        # 2. Reload task parameters
        meta = _load_task_metadata(task_id)
        if meta is None:
            _log(worker_id, task_id, "task_load_failed", "task missing after claim")
            return
        task_type, payload, timeout_seconds = meta

        _log(worker_id, task_id, "task_started", f"type={task_type} timeout={timeout_seconds}s")

        # 3. Resolve handler
        handler = HANDLERS.get(task_type)
        if handler is None:
            raise NonRetryableError(f"Unknown task type '{task_type}'. Not registered.")

        # 4. Execute handler with timeout enforcement (non-blocking on timeout)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(handler, payload)
        try:
            result = future.result(timeout=timeout_seconds)
            _transition_to_success(worker_id, task_id, result)
            executor.shutdown(wait=False)
        except concurrent.futures.TimeoutError:
            executor.shutdown(wait=False, cancel_futures=True)
            raise TaskTimeoutError(f"Task exceeded execution timeout of {timeout_seconds} seconds") from None
        except Exception:
            executor.shutdown(wait=False)
            raise

    except Exception as exc:  # noqa: BLE001
        _handle_task_failure(worker_id, task_id, exc)

    finally:
        with _current_task_lock:
            _current_task_id = None
        _try_advance_workflow(task_id)


# ---------------------------------------------------------------------------
# Main worker loop
# ---------------------------------------------------------------------------

def run(worker_id: str) -> None:
    """Start the Phase 4 worker process."""
    global _worker_id
    _worker_id = worker_id

    _log(worker_id, None, "worker_starting", "=" * 50)
    _log(worker_id, None, "worker_starting", f"Phase 4 reliable worker — id={worker_id}")
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

    # Register worker in registry
    _register_worker(worker_id)

    # Start background heartbeat & maintenance thread
    maintenance_thread = WorkerMaintenanceThread(worker_id)
    maintenance_thread.start()

    _log(worker_id, None, "worker_ready", "waiting for tasks on 'task_queue'")

    try:
        while _running:
            try:
                task_id = consume_task(timeout_seconds=3)
            except Exception as exc:  # noqa: BLE001
                logger.error("worker_id=%s event=consume_error error=%r", worker_id, str(exc))
                continue

            if task_id is None:
                continue

            try:
                _process_task(worker_id, task_id)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "worker_id=%s task_id=%s event=unexpected_error error=%r",
                    worker_id, task_id, str(exc),
                )
    finally:
        maintenance_thread.stop()
        maintenance_thread.join(timeout=3.0)
        _unregister_worker(worker_id)

    _log(worker_id, None, "worker_stopped", "clean shutdown complete")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Distributed Task Platform — Reliable Worker Process",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--worker-id",
        default=os.environ.get("WORKER_ID") or _default_worker_id(),
        help="Unique worker identifier.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(args.worker_id)
