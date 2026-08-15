#!/usr/bin/env python
"""Phase 3 worker concurrency benchmark.

Measures wall-clock completion time for a fixed batch of sleep tasks using
different worker counts.  All measurements are real; no values are fabricated.

Usage (from the project root or backend/ directory, with venv activated):

    # From project root:
    python benchmarks/worker_concurrency.py

    # From backend/:
    python -m benchmarks.worker_concurrency

Prerequisites:
    - Redis running at REDIS_URL (default: redis://localhost:6379/0)
    - PostgreSQL running at DATABASE_URL
    - The FastAPI app does NOT need to be running.
    - Tasks are inserted directly into PostgreSQL by the benchmark.
    - Tasks are published directly to Redis by the benchmark.

Configuration (environment variables):
    DATABASE_URL  — PostgreSQL connection string (required)
    REDIS_URL     — Redis URL (default: redis://localhost:6379/0)
    TASK_SECONDS  — Sleep duration per task (default: 1.0)
    TASK_COUNT    — Number of tasks per batch (default: 8)
    WORKER_COUNTS — Comma-separated list of worker counts to test (default: 1,2,4)

Output:
    A table of measurements for each worker count, plus a brief analysis.
"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

# ---------------------------------------------------------------------------
# Benchmark imports — we import app modules directly so we can insert tasks
# without needing the API running.
# ---------------------------------------------------------------------------

# Allow running from the repo root (outside backend/).
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.join(os.path.dirname(_HERE), "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.db.database import SessionLocal, engine  # noqa: E402
from app.db.models.task import Task, TaskStatus  # noqa: E402
from app.queue.publisher import QUEUE_NAME, publish_task  # noqa: E402
from app.queue.redis_client import get_redis_client  # noqa: E402


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TASK_SLEEP_SECONDS = float(os.environ.get("TASK_SECONDS", "1.0"))
TASK_COUNT = int(os.environ.get("TASK_COUNT", "8"))
WORKER_COUNTS_STR = os.environ.get("WORKER_COUNTS", "1,2,4")
WORKER_COUNTS = [int(n.strip()) for n in WORKER_COUNTS_STR.split(",")]
WORKER_STARTUP_GRACE = 1.5  # seconds to let workers start before checking completion
TASK_TIMEOUT = max(TASK_SLEEP_SECONDS * TASK_COUNT * 2, 30.0)  # generous per-batch ceiling


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flush_test_queue() -> None:
    """Remove all messages from the task_queue."""
    rc = get_redis_client()
    rc.delete(QUEUE_NAME)


def _create_batch(project_id, count: int, sleep_seconds: float) -> list:
    """Insert `count` QUEUED tasks into PostgreSQL and publish to Redis."""
    task_ids = []
    with SessionLocal() as db:
        for _ in range(count):
            task = Task(
                project_id=project_id,
                type="sleep",
                payload={"seconds": sleep_seconds},
                status=TaskStatus.QUEUED,
                queued_at=datetime.now(tz=timezone.utc),
                priority="NORMAL",
                timeout_seconds=int(sleep_seconds * 10 + 30),
                max_retries=0,
            )
            db.add(task)
        db.commit()
        # Refresh all to get IDs.
        for task in db.query(Task).filter(Task.status == TaskStatus.QUEUED).all():
            if task.id not in task_ids:
                task_ids.append(task.id)
    # Enqueue all.
    for tid in task_ids:
        publish_task(tid)
    return task_ids


def _ensure_project() -> "uuid.UUID":
    """Create a throwaway project and user for the benchmark."""
    from app.db.models.project import Project
    from app.db.models.user import User
    with SessionLocal() as db:
        user = User(
            email=f"benchmark-{uuid4()}@benchmark.local",
            password_hash="x",
            role="developer",
        )
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, name=f"benchmark-{uuid4()}", status="ACTIVE")
        db.add(project)
        db.commit()
        db.refresh(project)
        return project.id


def _wait_for_all_terminal(task_ids: list, timeout: float) -> dict:
    """Poll PostgreSQL until all tasks are in a terminal state.

    Returns a dict: task_id -> task (ORM object, detached).
    """
    from app.db.models.task import TaskStatus as TS
    terminal = {TS.SUCCESS, TS.FAILED, TS.DEAD_LETTER, TS.TIMED_OUT, TS.CANCELLED}
    deadline = time.monotonic() + timeout
    results = {}

    remaining = set(str(t) for t in task_ids)
    while remaining and time.monotonic() < deadline:
        with SessionLocal() as db:
            for tid in list(remaining):
                task = db.get(Task, tid)
                if task and task.status in terminal:
                    db.expunge(task)
                    results[tid] = task
                    remaining.discard(tid)
        if remaining:
            time.sleep(0.2)

    if remaining:
        print(f"  WARNING: {len(remaining)} tasks did not complete within {timeout:.0f}s")
    return results


def _start_worker(worker_id: str) -> subprocess.Popen:
    env = {
        **os.environ,
        "DATABASE_URL": os.environ.get("DATABASE_URL", ""),
        "REDIS_URL": os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        "JWT_SECRET_KEY": os.environ.get("JWT_SECRET_KEY", "benchmark-secret-key-long-enough-32chars"),
        "ENVIRONMENT": "benchmark",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }
    return subprocess.Popen(
        [sys.executable, "-m", "app.workers.runtime", "--worker-id", worker_id],
        cwd=_BACKEND,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_workers(procs: list) -> None:
    for proc in procs:
        try:
            proc.terminate()
        except Exception:
            pass
    for proc in procs:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------

def run_benchmark() -> None:
    print("=" * 65)
    print("Phase 3 Worker Concurrency Benchmark")
    print("=" * 65)
    print(f"  Tasks per batch : {TASK_COUNT}")
    print(f"  Sleep per task  : {TASK_SLEEP_SECONDS:.1f}s")
    print(f"  Worker counts   : {WORKER_COUNTS}")
    print(f"  Theoretical min : {TASK_SLEEP_SECONDS:.1f}s (with infinite workers)")
    print(f"  Sequential time : {TASK_COUNT * TASK_SLEEP_SECONDS:.1f}s (1 worker)")
    print()

    project_id = _ensure_project()
    results = []

    for worker_count in WORKER_COUNTS:
        print(f"--- {worker_count} worker(s) ---")
        _flush_test_queue()

        # Create a fresh batch of tasks.
        task_ids = _create_batch(project_id, TASK_COUNT, TASK_SLEEP_SECONDS)
        print(f"  Created {len(task_ids)} QUEUED tasks.")

        # Start workers.
        procs = [_start_worker(f"bench-worker-{i+1}") for i in range(worker_count)]
        time.sleep(WORKER_STARTUP_GRACE)  # Let workers connect and start BLPOP.

        wall_start = time.monotonic()
        task_results = _wait_for_all_terminal(task_ids, timeout=TASK_TIMEOUT)
        wall_elapsed = time.monotonic() - wall_start

        _stop_workers(procs)

        # Compute per-task stats.
        durations = []
        succeeded = 0
        failed = 0
        for tid, task in task_results.items():
            if task.status == TaskStatus.SUCCESS:
                succeeded += 1
                if task.started_at and task.finished_at:
                    dur = (task.finished_at - task.started_at).total_seconds()
                    durations.append(dur)
            else:
                failed += 1

        avg_dur = sum(durations) / len(durations) if durations else 0.0
        throughput = len(task_ids) / wall_elapsed if wall_elapsed > 0 else 0.0

        print(f"  Wall time       : {wall_elapsed:.2f}s")
        print(f"  Succeeded       : {succeeded}/{len(task_ids)}")
        print(f"  Failed          : {failed}")
        print(f"  Avg task dur    : {avg_dur:.3f}s")
        print(f"  Throughput      : {throughput:.2f} tasks/s")
        print()

        results.append({
            "worker_count": worker_count,
            "task_count": len(task_ids),
            "total_wall_time_s": round(wall_elapsed, 3),
            "succeeded": succeeded,
            "failed": failed,
            "average_task_duration_s": round(avg_dur, 3),
            "throughput_tasks_per_s": round(throughput, 3),
        })

    # Summary table
    print("=" * 65)
    print("SUMMARY")
    print("=" * 65)
    header = f"{'workers':>8}  {'tasks':>6}  {'wall(s)':>8}  {'ok':>5}  {'fail':>5}  {'tput(t/s)':>10}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['worker_count']:>8}  "
            f"{r['task_count']:>6}  "
            f"{r['total_wall_time_s']:>8.2f}  "
            f"{r['succeeded']:>5}  "
            f"{r['failed']:>5}  "
            f"{r['throughput_tasks_per_s']:>10.2f}"
        )

    print()
    if len(results) >= 2:
        r1 = next((r for r in results if r["worker_count"] == 1), None)
        r2 = next((r for r in results if r["worker_count"] >= 2), None)
        if r1 and r2:
            speedup = r1["total_wall_time_s"] / r2["total_wall_time_s"] if r2["total_wall_time_s"] > 0 else 0
            print(f"Speedup ({r2['worker_count']} workers vs 1 worker): {speedup:.2f}×")

    print()
    print("Note: Results include worker startup overhead and DB/Redis latency.")
    print("      Exact numbers vary by machine load and Redis/PostgreSQL performance.")


if __name__ == "__main__":
    run_benchmark()
