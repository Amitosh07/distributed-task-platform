#!/usr/bin/env python
"""Phase 5 workflow DAG & parallel branch benchmark.

Measures wall-clock completion time for:
1. Linear Workflow (A -> B -> C): sequential dependency chain.
2. Diamond DAG (A -> B, C -> D): parallel branches (B and C) followed by join (D).

Demonstrates that multi-worker execution accelerates parallel workflow branches (B & C run concurrently),
while maintaining strict sequential ordering for linear dependencies.

Usage:
    python benchmarks/workflow_dag_benchmark.py
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.join(os.path.dirname(_HERE), "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(_BACKEND, ".env"))

from app.db.database import SessionLocal  # noqa: E402
from app.db.models.project import Project  # noqa: E402
from app.db.models.user import User  # noqa: E402
from app.db.models.workflow import WorkflowRun, WorkflowRunStatus  # noqa: E402
from app.queue.publisher import QUEUE_NAME  # noqa: E402
from app.queue.redis_client import get_redis_client  # noqa: E402
from app.services.workflow_engine import create_workflow, start_workflow_run  # noqa: E402

TASK_SLEEP_SECONDS = float(os.environ.get("TASK_SECONDS", "0.5"))
WORKER_STARTUP_GRACE = 1.0


def _flush_test_queue() -> None:
    rc = get_redis_client()
    rc.delete(QUEUE_NAME)


def _ensure_user_and_project():
    with SessionLocal() as db:
        user = User(
            email=f"bench-wf-{uuid4()}@benchmark.local",
            password_hash="x",
            role="developer",
        )
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, name=f"bench-proj-{uuid4()}", status="ACTIVE")
        db.add(project)
        db.commit()
        db.refresh(user)
        db.refresh(project)
        return user, project.id


def _start_worker(worker_id: str) -> subprocess.Popen:
    env = {
        **os.environ,
        "DATABASE_URL": os.environ.get("DATABASE_URL", "postgresql+psycopg://postgres:<REDACTED>@localhost:5432/workflow_platform"),
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


def _wait_for_workflow_completion(run_id: UUID, timeout: float = 30.0) -> WorkflowRun:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            run = db.get(WorkflowRun, run_id)
            if run and run.status in (WorkflowRunStatus.SUCCESS, WorkflowRunStatus.FAILED):
                db.expunge(run)
                return run
        time.sleep(0.1)
    raise TimeoutError(f"Workflow run {run_id} did not complete within {timeout}s")


def run_benchmark():
    print("=" * 70)
    print("Phase 5 Workflow DAG & Parallel Branch Benchmark")
    print("=" * 70)
    print(f"  Task sleep duration : {TASK_SLEEP_SECONDS:.2f}s per node")
    print()

    user, project_id = _ensure_user_and_project()

    # 1. Define Linear Workflow: A -> B -> C (3 nodes)
    # Expected sequential duration: 3 * sleep_seconds
    with SessionLocal() as db:
        linear_wf = create_workflow(
            db=db,
            owner=user,
            project_id=project_id,
            name="Linear Pipeline (A->B->C)",
            nodes=[
                {"node_key": "A", "task_type": "sleep", "payload": {"seconds": TASK_SLEEP_SECONDS}},
                {"node_key": "B", "task_type": "sleep", "payload": {"seconds": TASK_SLEEP_SECONDS}},
                {"node_key": "C", "task_type": "sleep", "payload": {"seconds": TASK_SLEEP_SECONDS}},
            ],
            edges=[
                {"from": "A", "to": "B"},
                {"from": "B", "to": "C"},
            ],
        )
        linear_wf_id = linear_wf.id

    # 2. Define Diamond Workflow: A -> (B, C) -> D (4 nodes with 2 parallel branches)
    # Expected duration:
    #   1 worker  : A (1x) + B (1x) + C (1x) + D (1x) = 4 * sleep_seconds
    #   2+ workers: A (1x) + max(B, C) (1x) + D (1x) = 3 * sleep_seconds
    with SessionLocal() as db:
        diamond_wf = create_workflow(
            db=db,
            owner=user,
            project_id=project_id,
            name="Diamond DAG (A->B,C->D)",
            nodes=[
                {"node_key": "A", "task_type": "sleep", "payload": {"seconds": TASK_SLEEP_SECONDS}},
                {"node_key": "B", "task_type": "sleep", "payload": {"seconds": TASK_SLEEP_SECONDS}},
                {"node_key": "C", "task_type": "sleep", "payload": {"seconds": TASK_SLEEP_SECONDS}},
                {"node_key": "D", "task_type": "sleep", "payload": {"seconds": TASK_SLEEP_SECONDS}},
            ],
            edges=[
                {"from": "A", "to": "B"},
                {"from": "A", "to": "C"},
                {"from": "B", "to": "D"},
                {"from": "C", "to": "D"},
            ],
        )
        diamond_wf_id = diamond_wf.id

    results = []

    for test_name, wf_id, worker_count, exp_stages in [
        ("Linear A->B->C", linear_wf_id, 1, 3),
        ("Diamond DAG", diamond_wf_id, 1, 4),  # 1 worker executes B then C sequentially
        ("Diamond DAG", diamond_wf_id, 2, 3),  # 2 workers execute B & C in parallel
    ]:
        print(f"--- Running: {test_name} with {worker_count} worker(s) ---")
        _flush_test_queue()

        workers = [_start_worker(f"wf-bench-w{i+1}") for i in range(worker_count)]
        time.sleep(WORKER_STARTUP_GRACE)

        with SessionLocal() as db:
            run = start_workflow_run(db, user, wf_id)
            run_id = run.id

        start_time = time.monotonic()
        completed_run = _wait_for_workflow_completion(run_id, timeout=30.0)
        wall_time = time.monotonic() - start_time

        _stop_workers(workers)

        print(f"  Status    : {completed_run.status.value}")
        print(f"  Wall Time : {wall_time:.3f}s")
        print()

        results.append({
            "test_name": test_name,
            "workers": worker_count,
            "wall_time": wall_time,
            "status": completed_run.status.value,
        })

    # Summary
    print("=" * 70)
    print("BENCHMARK SUMMARY")
    print("=" * 70)
    print(f"{'Workflow Pattern':<25} {'Workers':>8} {'Wall Time (s)':>15} {'Status':>10}")
    print("-" * 62)
    for r in results:
        print(f"{r['test_name']:<25} {r['workers']:>8} {r['wall_time']:>15.3f} {r['status']:>10}")

    print()
    d1 = next(r for r in results if r["test_name"] == "Diamond DAG" and r["workers"] == 1)
    d2 = next(r for r in results if r["test_name"] == "Diamond DAG" and r["workers"] == 2)
    speedup = d1["wall_time"] / d2["wall_time"] if d2["wall_time"] > 0 else 0
    print(f"Diamond DAG Parallel Speedup (2 workers vs 1 worker): {speedup:.2f}×")
    print("=" * 70)


if __name__ == "__main__":
    run_benchmark()
