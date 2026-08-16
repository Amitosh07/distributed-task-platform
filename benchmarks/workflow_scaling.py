#!/usr/bin/env python
"""Phase 8 — Workflow DAG Scaling Benchmark.

Benchmarks the Phase 5 workflow engine with multiple DAG shapes
and worker counts. Measures total workflow duration, parallel speedup,
and verifies FAIL_FAST / CONTINUE failure policies.

DAG shapes:
    linear      A → B → C → D → E        (5 nodes, sequential)
    diamond     A → B → D                (4 nodes, 2 parallel branches)
                A → C → D
    fan_out     A → B, C, D → E          (5 nodes, 3 parallel branches)

Usage (from repo root, with backend .venv active):

    python benchmarks/workflow_scaling.py [--workers 1,2,3] [--seconds 0.5]
    python benchmarks/workflow_scaling.py --dag diamond --workers 1,2,3
    python benchmarks/workflow_scaling.py --dag all     --workers 1,2,3
    python benchmarks/workflow_scaling.py --test-failure-policies

Required environment:
    DATABASE_URL — PostgreSQL connection string
    REDIS_URL    — Redis URL (default: redis://localhost:6379/0)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from uuid import UUID

# Bootstrap path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from lib.common import (
    _BACKEND,
    ensure_bench_user_and_project,
    flush_bench_queue,
    print_header,
    print_section,
    save_result,
    start_worker,
    stop_workers,
)

_BACKEND_STR = str(_BACKEND)
if _BACKEND_STR not in sys.path:
    sys.path.insert(0, _BACKEND_STR)


# ---------------------------------------------------------------------------
# Workflow creation helpers
# ---------------------------------------------------------------------------

def _make_linear_workflow(db, user, project_id, task_seconds: float):
    """A → B → C → D → E (5 nodes, strictly sequential)."""
    from app.services.workflow_engine import create_workflow
    return create_workflow(
        db=db, owner=user, project_id=project_id,
        name=f"linear-{task_seconds}s",
        nodes=[
            {"node_key": "A", "task_type": "sleep", "payload": {"seconds": task_seconds}},
            {"node_key": "B", "task_type": "sleep", "payload": {"seconds": task_seconds}},
            {"node_key": "C", "task_type": "sleep", "payload": {"seconds": task_seconds}},
            {"node_key": "D", "task_type": "sleep", "payload": {"seconds": task_seconds}},
            {"node_key": "E", "task_type": "sleep", "payload": {"seconds": task_seconds}},
        ],
        edges=[
            {"from": "A", "to": "B"},
            {"from": "B", "to": "C"},
            {"from": "C", "to": "D"},
            {"from": "D", "to": "E"},
        ],
    )


def _make_diamond_workflow(db, user, project_id, task_seconds: float):
    """A → B → D  /  A → C → D (4 nodes, B and C parallel)."""
    from app.services.workflow_engine import create_workflow
    return create_workflow(
        db=db, owner=user, project_id=project_id,
        name=f"diamond-{task_seconds}s",
        nodes=[
            {"node_key": "A", "task_type": "sleep", "payload": {"seconds": task_seconds}},
            {"node_key": "B", "task_type": "sleep", "payload": {"seconds": task_seconds}},
            {"node_key": "C", "task_type": "sleep", "payload": {"seconds": task_seconds}},
            {"node_key": "D", "task_type": "sleep", "payload": {"seconds": task_seconds}},
        ],
        edges=[
            {"from": "A", "to": "B"},
            {"from": "A", "to": "C"},
            {"from": "B", "to": "D"},
            {"from": "C", "to": "D"},
        ],
    )


def _make_fan_out_workflow(db, user, project_id, task_seconds: float):
    """A → B, C, D → E (5 nodes, 3 parallel branches B/C/D)."""
    from app.services.workflow_engine import create_workflow
    return create_workflow(
        db=db, owner=user, project_id=project_id,
        name=f"fan-out-{task_seconds}s",
        nodes=[
            {"node_key": "A", "task_type": "sleep", "payload": {"seconds": task_seconds}},
            {"node_key": "B", "task_type": "sleep", "payload": {"seconds": task_seconds}},
            {"node_key": "C", "task_type": "sleep", "payload": {"seconds": task_seconds}},
            {"node_key": "D", "task_type": "sleep", "payload": {"seconds": task_seconds}},
            {"node_key": "E", "task_type": "sleep", "payload": {"seconds": task_seconds}},
        ],
        edges=[
            {"from": "A", "to": "B"},
            {"from": "A", "to": "C"},
            {"from": "A", "to": "D"},
            {"from": "B", "to": "E"},
            {"from": "C", "to": "E"},
            {"from": "D", "to": "E"},
        ],
    )


# ---------------------------------------------------------------------------
# Run one workflow and measure wall time
# ---------------------------------------------------------------------------

def _wait_for_workflow(run_id: UUID, timeout: float = 60.0):
    """Poll until workflow run reaches a terminal status."""
    from app.db.database import SessionLocal
    from app.db.models.workflow import WorkflowRun, WorkflowRunStatus

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            run = db.get(WorkflowRun, run_id)
            if run and run.status in (WorkflowRunStatus.SUCCESS, WorkflowRunStatus.FAILED):
                db.expunge(run)
                return run
        time.sleep(0.1)

    raise TimeoutError(f"Workflow run {run_id} did not complete within {timeout}s")


def _run_workflow_benchmark(
    user, wf_id, worker_count: int, dag_name: str, task_seconds: float
) -> dict:
    """Start workers, trigger a workflow run, wait for completion, return stats."""
    from app.db.database import SessionLocal
    from app.services.workflow_engine import start_workflow_run

    flush_bench_queue()
    workers = [start_worker(f"wf-{dag_name}-w{i+1}") for i in range(worker_count)]
    time.sleep(1.5)

    with SessionLocal() as db:
        run = start_workflow_run(db, user, wf_id)
        run_id = run.id

    wall_start = time.monotonic()
    timeout = (dag_name == "linear" and task_seconds * 6 + 15) or task_seconds * 5 + 15

    try:
        completed = _wait_for_workflow(run_id, timeout=timeout)
        wall_time = time.monotonic() - wall_start
        status = completed.status.value
    except TimeoutError:
        wall_time = time.monotonic() - wall_start
        status = "TIMEOUT"
    finally:
        stop_workers(workers)

    return {
        "dag": dag_name,
        "worker_count": worker_count,
        "wall_time_s": round(wall_time, 3),
        "status": status,
        "task_seconds": task_seconds,
    }


# ---------------------------------------------------------------------------
# DAG scaling benchmark
# ---------------------------------------------------------------------------

def run_dag_scaling(
    dag_names: list[str],
    worker_counts: list[int],
    task_seconds: float,
) -> None:
    print_header("Phase 8 — Workflow DAG Scaling Benchmark")
    print(f"  DAG shapes      : {dag_names}")
    print(f"  Worker counts   : {worker_counts}")
    print(f"  Task seconds    : {task_seconds}")

    from app.db.database import SessionLocal

    user, project_id = ensure_bench_user_and_project("wf-scaling")

    # Pre-create all workflow definitions
    dag_factories = {
        "linear": _make_linear_workflow,
        "diamond": _make_diamond_workflow,
        "fan_out": _make_fan_out_workflow,
    }

    wf_ids = {}
    with SessionLocal() as db:
        for dag in dag_names:
            wf = dag_factories[dag](db, user, project_id, task_seconds)
            wf_ids[dag] = wf.id

    results = []

    for dag_name in dag_names:
        for wc in worker_counts:
            print_section(f"{dag_name} DAG × {wc} worker(s)")
            r = _run_workflow_benchmark(user, wf_ids[dag_name], wc, dag_name, task_seconds)
            print(f"    Wall time : {r['wall_time_s']:.3f}s")
            print(f"    Status    : {r['status']}")

            # Theoretical speedup notes
            if dag_name == "diamond":
                # 1w: A+B+C+D = 4 nodes sequential
                # 2w: A + max(B,C) + D = 3 nodes
                print(f"    Theory    : {'4×' if wc == 1 else '3×'} task_seconds = "
                      f"{task_seconds * (4 if wc == 1 else 3):.2f}s")
            elif dag_name == "fan_out":
                # 1w: A+B+C+D+E = 5 nodes sequential
                # 3w: A + max(B,C,D) + E = 3 nodes
                print(f"    Theory    : {'5×' if wc == 1 else '3×'} task_seconds = "
                      f"{task_seconds * (5 if wc == 1 else 3):.2f}s")
            elif dag_name == "linear":
                # Always 5 nodes sequential regardless of workers
                print(f"    Theory    : 5× task_seconds = {task_seconds * 5:.2f}s (sequential, workers don't help)")

            results.append(r)

    # Summary table
    print()
    print("=" * 70)
    print("WORKFLOW SCALING SUMMARY")
    print("=" * 70)
    hdr = f"{'DAG':<12} {'Workers':>8} {'Wall(s)':>10} {'Status':>10}"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        print(f"{r['dag']:<12} {r['worker_count']:>8} {r['wall_time_s']:>10.3f} {r['status']:>10}")

    # Speedup analysis for diamond DAG
    diamond_results = [r for r in results if r["dag"] == "diamond"]
    if len(diamond_results) >= 2:
        d1 = next((r for r in diamond_results if r["worker_count"] == 1), None)
        if d1:
            print()
            print("Diamond DAG speedup:")
            for r in diamond_results:
                if r["worker_count"] == 1:
                    continue
                speedup = d1["wall_time_s"] / r["wall_time_s"] if r["wall_time_s"] > 0 else 0
                print(f"  {r['worker_count']} workers vs 1: speedup={speedup:.2f}×  wall={r['wall_time_s']:.3f}s")

    # Fan-out speedup
    fanout_results = [r for r in results if r["dag"] == "fan_out"]
    if len(fanout_results) >= 2:
        f1 = next((r for r in fanout_results if r["worker_count"] == 1), None)
        if f1:
            print()
            print("Fan-out DAG speedup:")
            for r in fanout_results:
                if r["worker_count"] == 1:
                    continue
                speedup = f1["wall_time_s"] / r["wall_time_s"] if r["wall_time_s"] > 0 else 0
                print(f"  {r['worker_count']} workers vs 1: speedup={speedup:.2f}×  wall={r['wall_time_s']:.3f}s")

    save_result("workflow_dag_scaling", {
        "task_seconds": task_seconds,
        "runs": results,
    })


# ---------------------------------------------------------------------------
# Failure policy benchmarks
# ---------------------------------------------------------------------------

def run_failure_policy_benchmarks(task_seconds: float) -> None:
    print_header("Phase 8 — Workflow Failure Policy Benchmarks")

    from app.db.database import SessionLocal
    from app.db.models.workflow import FailurePolicy, WorkflowRunStatus
    from app.services.workflow_engine import create_workflow, start_workflow_run

    user, project_id = ensure_bench_user_and_project("wf-failure-policy")
    results = []

    # ---- FAIL_FAST: branch fails → workflow fails ----
    print_section("FAIL_FAST — one branch fails")
    with SessionLocal() as db:
        wf = create_workflow(
            db=db, owner=user, project_id=project_id,
            name="fail-fast-test",
            failure_policy="FAIL_FAST",
            nodes=[
                {"node_key": "A", "task_type": "sleep", "payload": {"seconds": 0.1}},
                # B uses http_check with an invalid URL → will fail (NonRetryableError or connection error)
                {"node_key": "B", "task_type": "http_check", "payload": {"url": "http://localhost:19999/nonexistent"}},
                {"node_key": "C", "task_type": "sleep", "payload": {"seconds": task_seconds}},
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
    workers = [start_worker(f"ff-w{i+1}") for i in range(2)]
    time.sleep(1.5)

    with SessionLocal() as db:
        run = start_workflow_run(db, user, wf_id)
        run_id = run.id

    wall_start = time.monotonic()
    try:
        completed = _wait_for_workflow(run_id, timeout=30.0)
        wall_time = time.monotonic() - wall_start
        ff_status = completed.status.value
    except TimeoutError:
        wall_time = time.monotonic() - wall_start
        ff_status = "TIMEOUT"
    finally:
        stop_workers(workers)

    # Inspect node states
    from app.db.database import SessionLocal
    from app.db.models.workflow import WorkflowRun, WorkflowRunNodeStatus
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        node_states = {n.workflow_node.node_key: n.status.value for n in run.run_nodes}

    print(f"    Wall time     : {wall_time:.3f}s")
    print(f"    Final status  : {ff_status}")
    print(f"    Node states   : {node_states}")
    expected_ff = ff_status == "FAILED"
    print(f"    {'✓' if expected_ff else '✗'} Workflow reached FAILED (expected for FAIL_FAST with failed branch)")

    results.append({
        "policy": "FAIL_FAST",
        "wall_time_s": round(wall_time, 3),
        "status": ff_status,
        "node_states": node_states,
    })

    # ---- CONTINUE: branch fails, independent branch completes ----
    print_section("CONTINUE — one branch fails, independent branch continues")
    with SessionLocal() as db:
        wf2 = create_workflow(
            db=db, owner=user, project_id=project_id,
            name="continue-test",
            failure_policy="CONTINUE",
            nodes=[
                {"node_key": "A", "task_type": "sleep", "payload": {"seconds": 0.1}},
                {"node_key": "B", "task_type": "http_check", "payload": {"url": "http://localhost:19999/nonexistent"}},
                {"node_key": "C", "task_type": "sleep", "payload": {"seconds": task_seconds}},
            ],
            edges=[
                {"from": "A", "to": "B"},
                {"from": "A", "to": "C"},
            ],
        )
        wf2_id = wf2.id

    flush_bench_queue()
    workers = [start_worker(f"cont-w{i+1}") for i in range(2)]
    time.sleep(1.5)

    with SessionLocal() as db:
        run2 = start_workflow_run(db, user, wf2_id)
        run2_id = run2.id

    wall_start2 = time.monotonic()
    try:
        completed2 = _wait_for_workflow(run2_id, timeout=30.0)
        wall_time2 = time.monotonic() - wall_start2
        cont_status = completed2.status.value
    except TimeoutError:
        wall_time2 = time.monotonic() - wall_start2
        cont_status = "TIMEOUT"
    finally:
        stop_workers(workers)

    with SessionLocal() as db:
        run2 = db.get(WorkflowRun, run2_id)
        node_states2 = {n.workflow_node.node_key: n.status.value for n in run2.run_nodes}

    print(f"    Wall time     : {wall_time2:.3f}s")
    print(f"    Final status  : {cont_status}")
    print(f"    Node states   : {node_states2}")
    # B should fail, C should succeed (independent branch)
    c_ok = node_states2.get("C") == "SUCCESS"
    b_fail = node_states2.get("B") in ("FAILED", "SKIPPED")
    print(f"    {'✓' if c_ok else '✗'} Node C (independent branch) succeeded")
    print(f"    {'✓' if b_fail else '✗'} Node B (failed branch) is in terminal failure state")

    results.append({
        "policy": "CONTINUE",
        "wall_time_s": round(wall_time2, 3),
        "status": cont_status,
        "node_states": node_states2,
    })

    save_result("workflow_failure_policies", {"runs": results})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 8 workflow DAG scaling benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--dag", default="all",
                   help="DAG shape: linear, diamond, fan_out, or all (default: all)")
    p.add_argument("--workers", default="1,2,3",
                   help="Comma-separated worker counts for scaling sweep (default: 1,2,3)")
    p.add_argument("--seconds", type=float, default=0.5,
                   help="Sleep seconds per node (default: 0.5)")
    p.add_argument("--test-failure-policies", action="store_true",
                   help="Run FAIL_FAST and CONTINUE failure policy benchmarks")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    worker_counts = [int(x.strip()) for x in args.workers.split(",") if x.strip()]
    task_seconds = args.seconds

    all_dags = ["linear", "diamond", "fan_out"]
    if args.dag == "all":
        dag_names = all_dags
    elif args.dag in all_dags:
        dag_names = [args.dag]
    else:
        print(f"Unknown DAG: {args.dag}. Choose from: {all_dags}")
        sys.exit(1)

    run_dag_scaling(dag_names, worker_counts, task_seconds)

    if args.test_failure_policies:
        run_failure_policy_benchmarks(task_seconds)


if __name__ == "__main__":
    main()
