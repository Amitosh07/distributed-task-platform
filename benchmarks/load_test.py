#!/usr/bin/env python
"""Phase 8 — Unified Load-Test & Scaling Benchmark.

Measures throughput, latency, and queue behaviour under configurable load,
then generates a machine-readable JSON result artifact.

Usage (from repo root, with backend .venv active):

    python benchmarks/load_test.py [options]

Required environment:
    DATABASE_URL — PostgreSQL connection string
    REDIS_URL    — Redis URL (default: redis://localhost:6379/0)

Key options (all have defaults):
    --tasks         N   Number of tasks per batch    (default: 100)
    --workers       N   Number of concurrent workers (default: 3)
    --task-type     T   Task handler type            (default: sleep)
    --seconds       F   Sleep duration per task      (default: 1.0)
    --concurrency   N   Concurrent submission threads (default: 1)
    --warm-up       N   Warm-up tasks (discarded)    (default: 0)
    --worker-counts W   Comma-separated worker counts for scaling sweep
                        e.g. "1,2,3"  (overrides --workers for sweep mode)
    --output        D   Directory for JSON results   (default: benchmarks/results)

Examples:
    # Single run with 3 workers, 100 tasks
    python benchmarks/load_test.py --tasks 100 --workers 3

    # Scaling sweep (1, 2, 3 workers)
    python benchmarks/load_test.py --tasks 100 --worker-counts 1,2,3 --seconds 1

    # High concurrency submission test
    python benchmarks/load_test.py --tasks 200 --workers 3 --concurrency 20
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Bootstrap path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from lib.common import (
    create_tasks_batch,
    ensure_bench_project,
    flush_bench_queue,
    get_queue_depth,
    latency_stats,
    print_header,
    print_latency_table,
    print_section,
    save_result,
    start_worker,
    stop_workers,
    wait_for_terminal,
    _BACKEND,
)

_BACKEND_STR = str(_BACKEND)
if _BACKEND_STR not in sys.path:
    sys.path.insert(0, _BACKEND_STR)


# ---------------------------------------------------------------------------
# Single benchmark run
# ---------------------------------------------------------------------------

def run_single(
    *,
    project_id,
    worker_count: int,
    task_count: int,
    task_type: str,
    task_seconds: float,
    concurrency: int,
    warm_up: int,
    timeout_seconds: int,
    max_retries: int,
    label: str = "",
) -> dict:
    """Execute one benchmark run. Returns result dict."""
    from app.db.models.task import TaskStatus

    flush_bench_queue()

    # ---- Warm-up (tasks are submitted but stats are discarded) ----
    if warm_up > 0:
        print(f"    Warm-up: submitting {warm_up} tasks...")
        procs_wu = [start_worker(f"wu-w{i+1}") for i in range(worker_count)]
        time.sleep(1.5)
        wu_ids = create_tasks_batch(
            project_id,
            warm_up,
            task_type=task_type,
            payload={"seconds": task_seconds},
            timeout_seconds=timeout_seconds,
            max_retries=0,
        )
        wait_for_terminal(wu_ids, timeout=warm_up * task_seconds * 3 + 30)
        stop_workers(procs_wu)
        flush_bench_queue()
        print("    Warm-up complete.")

    # ---- Start workers ----
    procs = [start_worker(f"lt-w{i+1}") for i in range(worker_count)]
    time.sleep(1.5)  # Let workers register + connect

    # ---- Submit tasks (possibly concurrently) ----
    submit_start = time.monotonic()
    queue_depth_initial = get_queue_depth()

    if concurrency <= 1:
        task_ids = create_tasks_batch(
            project_id,
            task_count,
            task_type=task_type,
            payload={"seconds": task_seconds},
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        submission_times = [time.monotonic() - submit_start] * task_count
    else:
        # Split tasks across threads
        per_thread = max(1, task_count // concurrency)
        all_ids = []
        sub_times = []

        def _submit_chunk(chunk_size: int):
            t0 = time.monotonic()
            ids = create_tasks_batch(
                project_id,
                chunk_size,
                task_type=task_type,
                payload={"seconds": task_seconds},
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )
            elapsed = time.monotonic() - t0
            return ids, elapsed

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            chunks = []
            remaining = task_count
            for _ in range(concurrency):
                c = min(per_thread, remaining)
                if c > 0:
                    chunks.append(c)
                    remaining -= c

            futures = [pool.submit(_submit_chunk, c) for c in chunks]
            for fut in as_completed(futures):
                ids, elapsed = fut.result()
                all_ids.extend(ids)
                sub_times.append(elapsed)

        task_ids = all_ids
        submission_times = sub_times

    submit_elapsed = time.monotonic() - submit_start
    submission_rate = len(task_ids) / submit_elapsed if submit_elapsed > 0 else 0.0
    queue_depth_max_observed = get_queue_depth()

    # ---- Wait for completion ----
    wall_start = time.monotonic()
    timeout_ceiling = task_count * task_seconds * 3 + 60
    results = wait_for_terminal(task_ids, timeout=timeout_ceiling)
    wall_elapsed = time.monotonic() - wall_start
    queue_depth_final = get_queue_depth()

    stop_workers(procs)

    # ---- Collect statistics ----
    from app.db.database import SessionLocal
    from app.db.models.task import Task

    exec_durations: list[float] = []
    e2e_durations: list[float] = []
    queue_wait_durations: list[float] = []
    succeeded = 0
    failed = 0
    timed_out = 0
    retried_count = 0

    with SessionLocal() as db:
        for tid_str in (str(tid) for tid in task_ids):
            task = db.get(Task, tid_str)
            if task is None:
                continue

            if task.status == TaskStatus.SUCCESS:
                succeeded += 1
            elif task.status == TaskStatus.TIMED_OUT:
                timed_out += 1
                failed += 1
            else:
                failed += 1

            if task.attempt_count > 1:
                retried_count += 1

            if task.started_at and task.finished_at:
                exec_durations.append(
                    (task.finished_at - task.started_at).total_seconds()
                )
            if task.queued_at and task.finished_at:
                e2e_durations.append(
                    (task.finished_at - task.queued_at).total_seconds()
                )
            if task.queued_at and task.started_at:
                queue_wait_durations.append(
                    (task.started_at - task.queued_at).total_seconds()
                )

    total_submitted = len(task_ids)
    throughput = total_submitted / wall_elapsed if wall_elapsed > 0 else 0.0

    exec_stats = latency_stats(exec_durations)
    e2e_stats = latency_stats(e2e_durations)
    queue_wait_stats = latency_stats(queue_wait_durations)

    return {
        "label": label or f"{worker_count}w_{task_count}t",
        "worker_count": worker_count,
        "task_count": total_submitted,
        "task_type": task_type,
        "task_seconds": task_seconds,
        "concurrency": concurrency,
        "submission_rate_tasks_per_sec": round(submission_rate, 3),
        "wall_time_seconds": round(wall_elapsed, 3),
        "throughput_tasks_per_sec": round(throughput, 3),
        "success_count": succeeded,
        "failure_count": failed,
        "timeout_count": timed_out,
        "retried_count": retried_count,
        "queue_depth_initial": queue_depth_initial,
        "queue_depth_max_observed": queue_depth_max_observed,
        "queue_depth_final": queue_depth_final,
        "latency_execution_ms": exec_stats,
        "latency_e2e_ms": e2e_stats,
        "latency_queue_wait_ms": queue_wait_stats,
    }


# ---------------------------------------------------------------------------
# Scaling sweep
# ---------------------------------------------------------------------------

def run_scaling_sweep(
    *,
    worker_counts: list[int],
    task_count: int,
    task_type: str,
    task_seconds: float,
    concurrency: int,
    warm_up: int,
    timeout_seconds: int,
    max_retries: int,
    output_dir: Path,
) -> None:
    print_header("Phase 8 — Worker Scaling Benchmark")
    print(f"  Tasks per run   : {task_count}")
    print(f"  Task type       : {task_type}")
    print(f"  Sleep seconds   : {task_seconds}")
    print(f"  Worker counts   : {worker_counts}")
    print(f"  Concurrency     : {concurrency}")

    project_id = ensure_bench_project("loadtest")
    all_results = []

    for wc in worker_counts:
        print_section(f"{wc} worker(s)")
        r = run_single(
            project_id=project_id,
            worker_count=wc,
            task_count=task_count,
            task_type=task_type,
            task_seconds=task_seconds,
            concurrency=concurrency,
            warm_up=warm_up,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            label=f"{wc}w",
        )
        print(f"  Wall time       : {r['wall_time_seconds']:.2f}s")
        print(f"  Throughput      : {r['throughput_tasks_per_sec']:.3f} tasks/s")
        print(f"  Success         : {r['success_count']}/{r['task_count']}")
        print(f"  Failed          : {r['failure_count']}")
        print_latency_table(r["latency_e2e_ms"], "End-to-end latency")
        print_latency_table(r["latency_execution_ms"], "Execution latency")
        print_latency_table(r["latency_queue_wait_ms"], "Queue wait latency")
        all_results.append(r)

    # ---- Summary table ----
    print()
    print("=" * 80)
    print("SCALING SUMMARY")
    print("=" * 80)
    hdr = f"{'Workers':>8}  {'Tasks':>6}  {'Wall(s)':>8}  {'tput(t/s)':>10}  {'p95 e2e(ms)':>13}  {'ok':>5}  {'fail':>5}"
    print(hdr)
    print("-" * len(hdr))
    t1_wall = None
    for r in all_results:
        if r["worker_count"] == 1:
            t1_wall = r["wall_time_seconds"]
        print(
            f"{r['worker_count']:>8}  "
            f"{r['task_count']:>6}  "
            f"{r['wall_time_seconds']:>8.2f}  "
            f"{r['throughput_tasks_per_sec']:>10.3f}  "
            f"{r['latency_e2e_ms']['p95']:>13.1f}  "
            f"{r['success_count']:>5}  "
            f"{r['failure_count']:>5}"
        )

    if t1_wall and len(all_results) > 1:
        print()
        print("Speedup vs 1 worker:")
        for r in all_results:
            if r["worker_count"] == 1:
                continue
            n = r["worker_count"]
            speedup = t1_wall / r["wall_time_seconds"] if r["wall_time_seconds"] > 0 else 0
            efficiency = speedup / n
            print(
                f"  {n} workers: speedup={speedup:.2f}×  "
                f"efficiency={efficiency:.0%}  "
                f"wall={r['wall_time_seconds']:.2f}s"
            )

    # ---- Save JSON result ----
    save_result(
        "worker_scaling",
        {
            "runs": all_results,
            "speedup": {
                str(r["worker_count"]): round(t1_wall / r["wall_time_seconds"], 3)
                for r in all_results
                if t1_wall and r["wall_time_seconds"] > 0
            } if t1_wall else {},
        },
    )


# ---------------------------------------------------------------------------
# Single-configuration throughput test
# ---------------------------------------------------------------------------

def run_throughput_test(
    *,
    worker_count: int,
    task_count: int,
    task_type: str,
    task_seconds: float,
    concurrency: int,
    warm_up: int,
    timeout_seconds: int,
    max_retries: int,
    output_dir: Path,
) -> None:
    print_header("Phase 8 — Throughput Benchmark")
    print(f"  Workers         : {worker_count}")
    print(f"  Tasks           : {task_count}")
    print(f"  Task type       : {task_type}")
    print(f"  Sleep seconds   : {task_seconds}")
    print(f"  Concurrency     : {concurrency}")

    project_id = ensure_bench_project("throughput")
    r = run_single(
        project_id=project_id,
        worker_count=worker_count,
        task_count=task_count,
        task_type=task_type,
        task_seconds=task_seconds,
        concurrency=concurrency,
        warm_up=warm_up,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )

    print()
    print(f"  Wall time       : {r['wall_time_seconds']:.3f}s")
    print(f"  Submission rate : {r['submission_rate_tasks_per_sec']:.1f} tasks/s")
    print(f"  Throughput      : {r['throughput_tasks_per_sec']:.3f} tasks/s")
    print(f"  Success         : {r['success_count']}/{r['task_count']}")
    print(f"  Failed          : {r['failure_count']}")
    print(f"  Retried         : {r['retried_count']}")
    print(f"  Queue depth     : initial={r['queue_depth_initial']}  "
          f"max≈{r['queue_depth_max_observed']}  final={r['queue_depth_final']}")
    print_latency_table(r["latency_e2e_ms"], "End-to-end latency")
    print_latency_table(r["latency_execution_ms"], "Execution latency")
    print_latency_table(r["latency_queue_wait_ms"], "Queue wait latency")

    save_result("throughput", r)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 8 load-test and scaling benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--tasks", type=int, default=100, help="Tasks per batch (default: 100)")
    p.add_argument("--workers", type=int, default=3, help="Worker processes (default: 3)")
    p.add_argument("--worker-counts", default="", help="Comma-separated list for scaling sweep, e.g. 1,2,3")
    p.add_argument("--task-type", default="sleep", help="Task handler type (default: sleep)")
    p.add_argument("--seconds", type=float, default=1.0, help="Sleep seconds per task (default: 1.0)")
    p.add_argument("--concurrency", type=int, default=1, help="Concurrent submission threads (default: 1)")
    p.add_argument("--warm-up", type=int, default=0, help="Warm-up tasks (discarded, default: 0)")
    p.add_argument("--timeout", type=int, default=60, help="Task timeout_seconds (default: 60)")
    p.add_argument("--max-retries", type=int, default=0, help="Max retries per task (default: 0)")
    p.add_argument("--output", default=str(Path(__file__).parent / "results"), help="Results directory")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.worker_counts:
        counts = [int(x.strip()) for x in args.worker_counts.split(",") if x.strip()]
        run_scaling_sweep(
            worker_counts=counts,
            task_count=args.tasks,
            task_type=args.task_type,
            task_seconds=args.seconds,
            concurrency=args.concurrency,
            warm_up=args.warm_up,
            timeout_seconds=args.timeout,
            max_retries=args.max_retries,
            output_dir=output_dir,
        )
    else:
        run_throughput_test(
            worker_count=args.workers,
            task_count=args.tasks,
            task_type=args.task_type,
            task_seconds=args.seconds,
            concurrency=args.concurrency,
            warm_up=args.warm_up,
            timeout_seconds=args.timeout,
            max_retries=args.max_retries,
            output_dir=output_dir,
        )


if __name__ == "__main__":
    main()
