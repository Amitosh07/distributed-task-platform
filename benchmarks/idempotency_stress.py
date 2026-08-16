#!/usr/bin/env python
"""Phase 8 — Idempotency & Concurrent Submission Stress Test.

Tests:
1. Sequential duplicate submission: same idempotency key submitted N times.
   → Exactly 1 record in PostgreSQL.
2. Concurrent duplicate submission: same key submitted concurrently from N threads.
   → Exactly 1 record in PostgreSQL.
3. Concurrent bulk submission: N clients simultaneously submitting distinct tasks.
   → Correct task count, no database integrity violations.
4. Verify no race conditions in concurrent workflow run creation.

Usage (from repo root, with backend .venv active):

    python benchmarks/idempotency_stress.py [--concurrency 50] [--rounds 3]

Required environment:
    DATABASE_URL — PostgreSQL connection string
    REDIS_URL    — Redis URL (default: redis://localhost:6379/0)
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from uuid import UUID, uuid4

# Bootstrap path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from lib.common import (
    _BACKEND,
    ensure_bench_project,
    ensure_bench_user_and_project,
    flush_bench_queue,
    print_header,
    print_section,
    save_result,
    start_worker,
    stop_workers,
    wait_for_terminal,
    _now_utc,
)

_BACKEND_STR = str(_BACKEND)
if _BACKEND_STR not in sys.path:
    sys.path.insert(0, _BACKEND_STR)


# ---------------------------------------------------------------------------
# Test 1: Sequential duplicate submission
# ---------------------------------------------------------------------------

def test_sequential_idempotency(project_id, n_duplicates: int = 10) -> dict:
    """Submit the same idempotency key N times sequentially.
    Verify exactly 1 task record exists.
    """
    from app.db.database import SessionLocal
    from app.db.models.task import Task, TaskStatus
    from sqlalchemy import select

    ikey = f"seq-idem-{uuid4()}"
    created_ids = []
    was_created_flags = []

    for i in range(n_duplicates):
        with SessionLocal() as db:
            # Simulate what task_service.create_task does for idempotency
            existing = db.scalar(
                select(Task).where(
                    Task.project_id == project_id,
                    Task.idempotency_key == ikey,
                )
            )
            if existing is not None:
                created_ids.append(existing.id)
                was_created_flags.append(False)
                continue

            task = Task(
                project_id=project_id,
                type="sleep",
                payload={"seconds": 0.01},
                status=TaskStatus.QUEUED,
                queued_at=_now_utc(),
                priority="NORMAL",
                timeout_seconds=30,
                max_retries=0,
                idempotency_key=ikey,
            )
            db.add(task)
            try:
                db.commit()
                db.refresh(task)
                created_ids.append(task.id)
                was_created_flags.append(True)
            except Exception:
                db.rollback()
                existing = db.scalar(
                    select(Task).where(
                        Task.project_id == project_id,
                        Task.idempotency_key == ikey,
                    )
                )
                if existing:
                    created_ids.append(existing.id)
                    was_created_flags.append(False)

    # Verify DB record count
    with SessionLocal() as db:
        from sqlalchemy import func
        count = db.scalar(
            select(func.count(Task.id)).where(
                Task.project_id == project_id,
                Task.idempotency_key == ikey,
            )
        ) or 0

    unique_ids = len(set(str(i) for i in created_ids))
    created_true = sum(1 for x in was_created_flags if x)

    ok = count == 1 and created_true == 1

    return {
        "test": "sequential_idempotency",
        "n_duplicates": n_duplicates,
        "db_record_count": count,
        "unique_ids_returned": unique_ids,
        "was_created_once": created_true,
        "passed": ok,
    }


# ---------------------------------------------------------------------------
# Test 2: Concurrent duplicate submission
# ---------------------------------------------------------------------------

def test_concurrent_idempotency(project_id, n_threads: int = 20) -> dict:
    """Submit the same idempotency key concurrently from N threads.
    Only one task must be created.
    """
    from app.db.database import SessionLocal
    from app.db.models.task import Task, TaskStatus
    from sqlalchemy import select, func
    from sqlalchemy.exc import IntegrityError

    ikey = f"conc-idem-{uuid4()}"
    results = []

    def _submit_once():
        with SessionLocal() as db:
            existing = db.scalar(
                select(Task).where(
                    Task.project_id == project_id,
                    Task.idempotency_key == ikey,
                )
            )
            if existing is not None:
                return existing.id, False

            task = Task(
                project_id=project_id,
                type="sleep",
                payload={"seconds": 0.01},
                status=TaskStatus.QUEUED,
                queued_at=_now_utc(),
                priority="NORMAL",
                timeout_seconds=30,
                max_retries=0,
                idempotency_key=ikey,
            )
            db.add(task)
            try:
                db.commit()
                db.refresh(task)
                return task.id, True
            except IntegrityError:
                db.rollback()
                existing = db.scalar(
                    select(Task).where(
                        Task.project_id == project_id,
                        Task.idempotency_key == ikey,
                    )
                )
                if existing:
                    return existing.id, False
                return None, False

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = [pool.submit(_submit_once) for _ in range(n_threads)]
        for fut in as_completed(futures):
            results.append(fut.result())

    with SessionLocal() as db:
        count = db.scalar(
            select(func.count(Task.id)).where(
                Task.project_id == project_id,
                Task.idempotency_key == ikey,
            )
        ) or 0

    created_count = sum(1 for _, was_created in results if was_created)
    returned_ids = set(str(tid) for tid, _ in results if tid is not None)

    ok = count == 1 and len(returned_ids) == 1

    return {
        "test": "concurrent_idempotency",
        "n_threads": n_threads,
        "db_record_count": count,
        "created_count": created_count,
        "unique_ids_returned": len(returned_ids),
        "passed": ok,
    }


# ---------------------------------------------------------------------------
# Test 3: Concurrent bulk submission (distinct tasks)
# ---------------------------------------------------------------------------

def test_concurrent_bulk_submission(project_id, n_threads: int = 50) -> dict:
    """Submit N distinct tasks concurrently. Verify correct total count,
    no integrity violations, and all tasks eventually complete.
    """
    from app.db.database import SessionLocal
    from app.db.models.task import Task, TaskStatus
    from app.queue.publisher import publish_task
    from sqlalchemy import select, func

    submitted_ids = []
    errors = []

    def _submit_task():
        with SessionLocal() as db:
            task = Task(
                project_id=project_id,
                type="sleep",
                payload={"seconds": 0.05},
                status=TaskStatus.QUEUED,
                queued_at=_now_utc(),
                priority="NORMAL",
                timeout_seconds=30,
                max_retries=0,
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            tid = task.id
        publish_task(tid)
        return tid

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = [pool.submit(_submit_task) for _ in range(n_threads)]
        for fut in as_completed(futures):
            try:
                submitted_ids.append(fut.result())
            except Exception as e:
                errors.append(str(e))

    submitted_count = len(submitted_ids)

    # Start workers and execute all tasks
    workers = [start_worker(f"bulk-w{i+1}") for i in range(3)]
    time.sleep(1.5)
    results = wait_for_terminal(submitted_ids, timeout=60.0)
    stop_workers(workers)

    from app.db.models.task import TaskStatus as TS
    succeeded = sum(1 for t in results.values() if t.status == TS.SUCCESS)

    ok = submitted_count == n_threads and len(errors) == 0 and succeeded == n_threads

    return {
        "test": "concurrent_bulk_submission",
        "n_threads": n_threads,
        "submitted_count": submitted_count,
        "error_count": len(errors),
        "succeeded": succeeded,
        "passed": ok,
        "errors": errors[:5] if errors else [],
    }


# ---------------------------------------------------------------------------
# Test 4: Duplicate dispatch stress (workflow DAG)
# ---------------------------------------------------------------------------

def test_duplicate_dispatch_stress(n_runs: int = 20, worker_count: int = 3) -> dict:
    """Submit N diamond DAG workflow runs with multiple concurrent workers.
    Verify no workflow node is dispatched more than once.
    """
    from app.db.database import SessionLocal
    from app.db.models.workflow import WorkflowRun, WorkflowRunStatus
    from app.services.workflow_engine import create_workflow, start_workflow_run
    from sqlalchemy import select, func
    from app.db.models.task import Task

    user, project_id = ensure_bench_user_and_project("dispatch-stress")

    with SessionLocal() as db:
        wf = create_workflow(
            db=db, owner=user, project_id=project_id,
            name=f"dispatch-stress-diamond",
            nodes=[
                {"node_key": "A", "task_type": "sleep", "payload": {"seconds": 0.1}},
                {"node_key": "B", "task_type": "sleep", "payload": {"seconds": 0.1}},
                {"node_key": "C", "task_type": "sleep", "payload": {"seconds": 0.1}},
                {"node_key": "D", "task_type": "sleep", "payload": {"seconds": 0.1}},
            ],
            edges=[
                {"from": "A", "to": "B"},
                {"from": "A", "to": "C"},
                {"from": "B", "to": "D"},
                {"from": "C", "to": "D"},
            ],
        )
        wf_id = wf.id

    flush_bench_queue()
    workers = [start_worker(f"dd-w{i+1}") for i in range(worker_count)]
    time.sleep(1.5)

    # Start all workflow runs
    run_ids = []
    with SessionLocal() as db:
        for _ in range(n_runs):
            run = start_workflow_run(db, user, wf_id)
            run_ids.append(run.id)

    # Wait for all runs to complete
    deadline = time.monotonic() + 120.0
    completed = 0
    failed_runs = 0

    while time.monotonic() < deadline:
        with SessionLocal() as db:
            runs = [db.get(WorkflowRun, rid) for rid in run_ids]
            completed = sum(1 for r in runs if r and r.status == WorkflowRunStatus.SUCCESS)
            failed_runs = sum(1 for r in runs if r and r.status == WorkflowRunStatus.FAILED)
        if (completed + failed_runs) >= n_runs:
            break
        time.sleep(0.5)

    stop_workers(workers)

    # Check for duplicate task dispatch per node
    # Each workflow_run_node should have at most 1 task linked
    with SessionLocal() as db:
        from app.db.models.workflow import WorkflowRunNode
        # Count tasks per run_node_id
        from sqlalchemy import func
        duplicates = db.execute(
            select(Task.workflow_run_node_id, func.count(Task.id).label("cnt"))
            .where(Task.workflow_run_node_id.is_not(None))
            .group_by(Task.workflow_run_node_id)
            .having(func.count(Task.id) > 1)
        ).fetchall()

    duplicate_dispatch_count = len(duplicates)
    ok = duplicate_dispatch_count == 0 and completed == n_runs

    return {
        "test": "duplicate_dispatch_stress",
        "n_runs": n_runs,
        "worker_count": worker_count,
        "completed_runs": completed,
        "failed_runs": failed_runs,
        "duplicate_dispatch_count": duplicate_dispatch_count,
        "passed": ok,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_all(concurrency: int, rounds: int) -> None:
    print_header("Phase 8 — Idempotency & Concurrent Submission Stress Tests")

    project_id = ensure_bench_project("idempotency-bench")
    all_results = []

    # Test 1: Sequential idempotency
    print_section("Test 1: Sequential duplicate submission (10 duplicates)")
    r1 = test_sequential_idempotency(project_id, n_duplicates=10)
    print(f"    DB records      : {r1['db_record_count']} (expected: 1)")
    print(f"    was_created=True: {r1['was_created_once']} (expected: 1)")
    print(f"    {'✓ PASSED' if r1['passed'] else '✗ FAILED'}")
    all_results.append(r1)

    # Test 2: Concurrent idempotency
    print_section(f"Test 2: Concurrent duplicate submission ({concurrency} threads)")
    r2 = test_concurrent_idempotency(project_id, n_threads=concurrency)
    print(f"    DB records      : {r2['db_record_count']} (expected: 1)")
    print(f"    created_count   : {r2['created_count']} (expected: 1)")
    print(f"    unique IDs      : {r2['unique_ids_returned']} (expected: 1)")
    print(f"    {'✓ PASSED' if r2['passed'] else '✗ FAILED'}")
    all_results.append(r2)

    # Test 3: Concurrent bulk submission
    print_section(f"Test 3: Concurrent distinct submission ({concurrency} threads)")
    r3 = test_concurrent_bulk_submission(project_id, n_threads=concurrency)
    print(f"    Submitted       : {r3['submitted_count']} (expected: {concurrency})")
    print(f"    Errors          : {r3['error_count']} (expected: 0)")
    print(f"    Succeeded       : {r3['succeeded']} (expected: {concurrency})")
    print(f"    {'✓ PASSED' if r3['passed'] else '✗ FAILED'}")
    all_results.append(r3)

    # Test 4: Duplicate dispatch stress (run multiple times per rounds)
    for round_n in range(1, rounds + 1):
        print_section(f"Test 4 (round {round_n}/{rounds}): Duplicate dispatch stress — 20 diamond DAG runs × 3 workers")
        r4 = test_duplicate_dispatch_stress(n_runs=20, worker_count=3)
        print(f"    Completed runs  : {r4['completed_runs']}/20")
        print(f"    Failed runs     : {r4['failed_runs']}")
        print(f"    Duplicate dispatches: {r4['duplicate_dispatch_count']} (expected: 0)")
        print(f"    {'✓ PASSED' if r4['passed'] else '✗ FAILED'}")
        all_results.append(r4)

    # Summary
    passed = sum(1 for r in all_results if r["passed"])
    total = len(all_results)
    print()
    print("=" * 60)
    print(f"IDEMPOTENCY STRESS TEST SUMMARY: {passed}/{total} PASSED")
    print("=" * 60)

    save_result("idempotency_stress", {
        "concurrency": concurrency,
        "rounds": rounds,
        "results": all_results,
        "passed": passed,
        "total": total,
    })


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 8 idempotency and concurrent submission stress tests",
    )
    p.add_argument("--concurrency", type=int, default=20,
                   help="Number of concurrent threads for submission tests (default: 20)")
    p.add_argument("--rounds", type=int, default=2,
                   help="Number of rounds for duplicate dispatch stress test (default: 2)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    run_all(concurrency=args.concurrency, rounds=args.rounds)


if __name__ == "__main__":
    main()
