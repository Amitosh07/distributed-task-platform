"""Shared benchmark library for Phase 8.

Provides reusable helpers used by all benchmark scripts:
- Path setup so scripts can import from backend/app without installation
- Project/user fixture creation
- Task batch creation (direct DB insert + Redis publish)
- Worker subprocess management
- PostgreSQL polling for terminal state
- Latency statistics (min/avg/p50/p95/p99/max)
- Machine-readable JSON result persistence

Design principles:
- Never hard-code machine-specific paths.
- Never fabricate numbers — all statistics come from real timestamps.
- Never flush Redis DB 0; only delete the specific queue key.
- Always use environment variables for DB/Redis URLs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, quantiles
from typing import Any
from uuid import UUID, uuid4

# ---------------------------------------------------------------------------
# Path bootstrap — allow running from any directory
# ---------------------------------------------------------------------------

_BENCHMARKS_DIR = Path(__file__).resolve().parent.parent  # benchmarks/
_REPO_ROOT = _BENCHMARKS_DIR.parent                        # repo root
_BACKEND = _REPO_ROOT / "backend"

if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Load .env from backend/ so DATABASE_URL / REDIS_URL are available.
try:
    from dotenv import load_dotenv  # type: ignore[import]
    load_dotenv(_BACKEND / ".env")
except ImportError:
    pass  # python-dotenv not installed; environment must be set manually.

# ---------------------------------------------------------------------------
# Results directory
# ---------------------------------------------------------------------------

RESULTS_DIR = _BENCHMARKS_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Lazy app imports (only after path setup and .env load)
# ---------------------------------------------------------------------------

def _get_db_imports():
    from app.db.database import SessionLocal
    from app.db.models.project import Project
    from app.db.models.task import Task, TaskStatus
    from app.db.models.user import User
    from app.queue.publisher import QUEUE_NAME, publish_task
    from app.queue.redis_client import get_redis_client
    return SessionLocal, Project, Task, TaskStatus, User, QUEUE_NAME, publish_task, get_redis_client


# ---------------------------------------------------------------------------
# UTC helper
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Project/user fixture
# ---------------------------------------------------------------------------

def ensure_bench_project(label: str = "bench") -> UUID:
    """Create a throwaway user + project for benchmarks. Returns project_id."""
    SessionLocal, Project, Task, TaskStatus, User, *_ = _get_db_imports()
    with SessionLocal() as db:
        user = User(
            email=f"{label}-{uuid4()}@benchmark.local",
            password_hash="x",
            role="developer",
        )
        db.add(user)
        db.flush()
        project = Project(
            owner_id=user.id,
            name=f"{label}-{uuid4()}",
            status="ACTIVE",
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        return project.id


def ensure_bench_user_and_project(label: str = "bench") -> tuple[Any, UUID]:
    """Create a throwaway user + project. Returns (user_orm, project_id)."""
    SessionLocal, Project, Task, TaskStatus, User, *_ = _get_db_imports()
    with SessionLocal() as db:
        user = User(
            email=f"{label}-{uuid4()}@benchmark.local",
            password_hash="x",
            role="developer",
        )
        db.add(user)
        db.flush()
        project = Project(
            owner_id=user.id,
            name=f"{label}-{uuid4()}",
            status="ACTIVE",
        )
        db.add(project)
        db.commit()
        db.refresh(user)
        db.refresh(project)
        db.expunge_all()
        return user, project.id


# ---------------------------------------------------------------------------
# Task batch creation
# ---------------------------------------------------------------------------

def create_tasks_batch(
    project_id: UUID,
    count: int,
    task_type: str = "sleep",
    payload: dict | None = None,
    timeout_seconds: int = 60,
    max_retries: int = 0,
    priority: str = "NORMAL",
    idempotency_key_prefix: str | None = None,
) -> list[UUID]:
    """Insert *count* QUEUED tasks into PostgreSQL and publish each to Redis.

    Returns the list of task IDs created.
    """
    if payload is None:
        payload = {"seconds": 1.0}

    SessionLocal, Project, Task, TaskStatus, User, QUEUE_NAME, publish_task, get_redis_client = _get_db_imports()
    task_ids: list[UUID] = []

    with SessionLocal() as db:
        for i in range(count):
            ikey = f"{idempotency_key_prefix}-{i}" if idempotency_key_prefix else None
            task = Task(
                project_id=project_id,
                type=task_type,
                payload=payload,
                status=TaskStatus.QUEUED,
                queued_at=_now_utc(),
                priority=priority,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                idempotency_key=ikey,
            )
            db.add(task)
        db.commit()

        # Reload IDs
        from sqlalchemy import select
        tasks = db.scalars(
            select(Task).where(
                Task.project_id == project_id,
                Task.status == TaskStatus.QUEUED,
            ).order_by(Task.created_at.desc()).limit(count)
        ).all()
        task_ids = [t.id for t in tasks]

    # Publish all to Redis queue
    for tid in task_ids:
        publish_task(tid)

    return task_ids


# ---------------------------------------------------------------------------
# Wait for terminal state
# ---------------------------------------------------------------------------

TERMINAL_STATUSES: frozenset  # set lazily


def wait_for_terminal(
    task_ids: list[UUID],
    timeout: float = 120.0,
    poll_interval: float = 0.2,
) -> dict[str, Any]:
    """Poll PostgreSQL until all task IDs reach a terminal state.

    Returns dict: task_id_str -> Task (detached ORM object).
    """
    SessionLocal, Project, Task, TaskStatus, *_ = _get_db_imports()
    terminal = {
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.DEAD_LETTER,
        TaskStatus.TIMED_OUT,
        TaskStatus.CANCELLED,
    }

    deadline = time.monotonic() + timeout
    remaining = {str(tid) for tid in task_ids}
    results: dict[str, Any] = {}

    while remaining and time.monotonic() < deadline:
        with SessionLocal() as db:
            for tid_str in list(remaining):
                task = db.get(Task, tid_str)
                if task and task.status in terminal:
                    db.expunge(task)
                    results[tid_str] = task
                    remaining.discard(tid_str)
        if remaining:
            time.sleep(poll_interval)

    if remaining:
        print(f"  WARNING: {len(remaining)} tasks did not complete within {timeout:.0f}s")

    return results


# ---------------------------------------------------------------------------
# Worker subprocess management
# ---------------------------------------------------------------------------

def start_worker(
    worker_id: str,
    *,
    capture_output: bool = False,
    extra_env: dict | None = None,
) -> subprocess.Popen:
    """Launch a worker subprocess. Uses the same venv Python as the caller."""
    env = {
        **os.environ,
        "DATABASE_URL": os.environ.get("DATABASE_URL", ""),
        "REDIS_URL": os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        "JWT_SECRET_KEY": os.environ.get(
            "JWT_SECRET_KEY", "benchmark-secret-key-that-is-long-enough-32chars"
        ),
        "ENVIRONMENT": "benchmark",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        **(extra_env or {}),
    }
    stdout = subprocess.PIPE if capture_output else subprocess.DEVNULL
    stderr = subprocess.STDOUT if capture_output else subprocess.DEVNULL

    return subprocess.Popen(
        [sys.executable, "-m", "app.workers.runtime", "--worker-id", worker_id],
        cwd=str(_BACKEND),
        env=env,
        stdout=stdout,
        stderr=stderr,
    )


def stop_workers(procs: list[subprocess.Popen], timeout: float = 5.0) -> None:
    """Gracefully stop all worker processes (SIGTERM then SIGKILL)."""
    for proc in procs:
        try:
            proc.terminate()
        except Exception:
            pass
    for proc in procs:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait()
            except Exception:
                pass


def crash_worker(proc: subprocess.Popen) -> None:
    """Hard-kill a worker process to simulate an abrupt crash (SIGKILL)."""
    try:
        proc.kill()
        proc.wait(timeout=3)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Redis queue helpers
# ---------------------------------------------------------------------------

def flush_bench_queue() -> None:
    """Remove all messages from the task_queue Redis list.

    NEVER flushes the whole Redis server or touches other keys.
    """
    _, _, _, _, _, QUEUE_NAME, _, get_redis_client = _get_db_imports()
    get_redis_client().delete(QUEUE_NAME)


def get_queue_depth() -> int:
    """Return the current Redis task_queue depth (best effort; 0 on error)."""
    try:
        _, _, _, _, _, QUEUE_NAME, _, get_redis_client = _get_db_imports()
        return int(get_redis_client().llen(QUEUE_NAME))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Latency statistics
# ---------------------------------------------------------------------------

def latency_stats(durations_seconds: list[float]) -> dict[str, float]:
    """Return percentile statistics for a list of durations (in seconds).

    Returns a dict with keys: min, avg, p50, p95, p99, max.
    All values are in milliseconds.
    If the list is empty, all values are 0.
    """
    if not durations_seconds:
        return {"min": 0.0, "avg": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}

    ms = [d * 1000.0 for d in durations_seconds]
    ms_sorted = sorted(ms)
    n = len(ms_sorted)

    avg = sum(ms_sorted) / n
    p50 = median(ms_sorted)

    if n >= 20:
        qs = quantiles(ms_sorted, n=100)
        p95 = qs[94]
        p99 = qs[98]
    else:
        # For small samples, use simple percentile interpolation
        def _pct(data: list[float], p: float) -> float:
            idx = (p / 100.0) * (len(data) - 1)
            lo, hi = int(idx), min(int(idx) + 1, len(data) - 1)
            return data[lo] + (data[hi] - data[lo]) * (idx - lo)

        p95 = _pct(ms_sorted, 95)
        p99 = _pct(ms_sorted, 99)

    return {
        "min": round(ms_sorted[0], 2),
        "avg": round(avg, 2),
        "p50": round(p50, 2),
        "p95": round(p95, 2),
        "p99": round(p99, 2),
        "max": round(ms_sorted[-1], 2),
    }


# ---------------------------------------------------------------------------
# JSON result persistence
# ---------------------------------------------------------------------------

def save_result(benchmark_name: str, data: dict) -> Path:
    """Write benchmark result to benchmarks/results/<name>_<timestamp>.json.

    Returns the path of the written file.
    """
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{benchmark_name}_{timestamp}.json"
    path = RESULTS_DIR / filename

    result = {
        "benchmark": benchmark_name,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        **data,
    }

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, default=str)

    print(f"  Result saved → {path}")
    return path


# ---------------------------------------------------------------------------
# Terminal output helpers
# ---------------------------------------------------------------------------

def print_header(title: str) -> None:
    width = max(len(title) + 4, 70)
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def print_section(title: str) -> None:
    print(f"\n--- {title} ---")


def print_latency_table(stats: dict[str, float], label: str = "Latency") -> None:
    print(f"  {label}:")
    print(f"    min={stats['min']:.1f}ms  avg={stats['avg']:.1f}ms  "
          f"p50={stats['p50']:.1f}ms  p95={stats['p95']:.1f}ms  "
          f"p99={stats['p99']:.1f}ms  max={stats['max']:.1f}ms")
