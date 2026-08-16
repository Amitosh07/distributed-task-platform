"""Phase 3 — concurrency tests.

These tests verify:
1. Worker ID is accepted and propagated correctly.
2. Atomic task claim: two concurrent workers racing for the same task result in
   exactly one successful claimant.
3. Concurrent execution: multiple workers process tasks in parallel,
   demonstrating real wall-clock speedup.
4. Work distribution: tasks are spread across multiple workers.
5. All tasks eventually reach a terminal state (no double-execution or stuck tasks).

Test strategy:
- The atomic claim test uses threads to simulate two workers competing for a
  single task. It calls _atomic_claim directly to isolate the DB behaviour
  without needing to run full worker loops.
- The concurrency/distribution tests use real subprocess workers
  (python -m app.workers.runtime) to demonstrate process-level parallelism.
  This matches the real deployment model described in the PRD.
- Task duration is kept short (sleep 0.5s) to keep the suite fast while still
  demonstrating measurable concurrency.
"""

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.db.database import SessionLocal
from app.db.models.task import Task, TaskStatus
from app.workers.runtime import _atomic_claim

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires isolated PostgreSQL and Redis (TEST_DATABASE_URL must be set)",
)

_TEST_WORKER_ID = "test-worker-concurrency"


# ---------------------------------------------------------------------------
# Shared helpers (duplicated minimally to keep tests self-contained)
# ---------------------------------------------------------------------------

def _make_project_and_task(task_type: str = "sleep", payload: dict | None = None) -> tuple:
    """Create a project + QUEUED task in the test DB. Returns (project_id, task_id)."""
    from app.db.models.project import Project
    from app.db.models.user import User
    payload = payload or {"seconds": 0.5}
    with SessionLocal() as db:
        user = User(email=f"p3-test-{uuid4()}@example.com", password_hash="x", role="developer")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, name=f"proj-{uuid4()}", status="ACTIVE")
        db.add(project)
        db.flush()
        task = Task(
            project_id=project.id,
            type=task_type,
            payload=payload,
            status=TaskStatus.QUEUED,
            queued_at=datetime.now(tz=timezone.utc),
            priority="NORMAL",
            timeout_seconds=60,
            max_retries=0,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return project.id, task.id


def _load_task(task_id: UUID) -> Task:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        db.expunge(task)
        return task


def _wait_for_terminal(task_id: UUID, timeout: float = 30.0) -> Task:
    """Poll until the task is in a terminal state."""
    terminal = {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.DEAD_LETTER, TaskStatus.TIMED_OUT}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = _load_task(task_id)
        if task.status in terminal:
            return task
        time.sleep(0.1)
    task = _load_task(task_id)
    raise AssertionError(
        f"Task {task_id} did not reach a terminal state within {timeout}s. "
        f"Current status: {task.status}"
    )


def _enqueue_task_id(task_id: UUID) -> None:
    """Push a task ID onto the Redis test queue."""
    import json as _json
    from app.queue.publisher import QUEUE_NAME
    from app.queue.redis_client import get_redis_client
    client = get_redis_client()
    client.rpush(QUEUE_NAME, _json.dumps({"task_id": str(task_id)}))


# ---------------------------------------------------------------------------
# 1. Worker ID tests
# ---------------------------------------------------------------------------

class TestWorkerId:
    def test_default_worker_id_is_generated(self):
        """Worker generates a non-empty default ID from hostname + PID."""
        from app.workers.runtime import _default_worker_id
        wid = _default_worker_id()
        assert wid, "Default worker ID must be non-empty"
        assert "-" in wid, "Default worker ID should contain hostname-PID separator"

    def test_cli_worker_id_accepted(self):
        """--worker-id argument is parsed correctly."""
        from app.workers.runtime import _parse_args
        args = _parse_args.__wrapped__() if hasattr(_parse_args, "__wrapped__") else None
        # Test via subprocess to avoid contaminating argparse state
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.argv = ['runtime', '--worker-id', 'my-worker'];"
             "from app.workers.runtime import _parse_args; "
             "args = _parse_args(); print(args.worker_id)"],
            capture_output=True, text=True,
            cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
            env={**os.environ},
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "my-worker"

    def test_env_worker_id_accepted(self):
        """WORKER_ID env variable is used as default when --worker-id is not supplied."""
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.argv = ['runtime'];"
             "from app.workers.runtime import _parse_args; "
             "args = _parse_args(); print(args.worker_id)"],
            capture_output=True, text=True,
            cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
            env={**os.environ, "WORKER_ID": "env-worker-42"},
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "env-worker-42"


# ---------------------------------------------------------------------------
# 2. Atomic task claim tests (thread-level simulation)
# ---------------------------------------------------------------------------

class TestAtomicClaim:
    def test_single_worker_claims_task(self):
        """A single worker can claim a QUEUED task."""
        _, task_id = _make_project_and_task("sleep", {"seconds": 0.01})
        result = _atomic_claim("worker-solo", task_id)
        assert result is True
        task = _load_task(task_id)
        assert task.status == TaskStatus.RUNNING
        assert task.attempt_count == 1
        assert task.started_at is not None

    def test_claim_fails_if_task_not_queued(self):
        """Claiming an already-RUNNING task must fail."""
        _, task_id = _make_project_and_task("sleep", {"seconds": 0.01})
        # First claim succeeds.
        assert _atomic_claim("worker-1", task_id) is True
        # Second claim on the same task must fail (status is now RUNNING).
        assert _atomic_claim("worker-2", task_id) is False

    def test_claim_fails_for_nonexistent_task(self):
        """Claiming a non-existent task ID must return False (0 rows affected)."""
        result = _atomic_claim("worker-x", uuid4())
        assert result is False

    def test_two_workers_racing_only_one_wins(self):
        """Race condition: two threads competing for the same task — exactly one wins.

        This is the critical Phase 3 correctness test. Using threads (not processes)
        gives deterministic concurrent DB access within the same Python process.
        Both threads call _atomic_claim at the same time against the same QUEUED task.
        PostgreSQL's row-level locking ensures exactly one UPDATE succeeds.
        """
        _, task_id = _make_project_and_task("sleep", {"seconds": 0.01})

        results = []

        def claim(worker_id: str) -> bool:
            return _atomic_claim(worker_id, task_id)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(claim, "racer-A"),
                executor.submit(claim, "racer-B"),
            ]
            for f in as_completed(futures):
                results.append(f.result())

        # Exactly one worker must have claimed the task.
        assert results.count(True) == 1, (
            f"Expected exactly 1 successful claim, got: {results}"
        )
        assert results.count(False) == 1, (
            f"Expected exactly 1 failed claim, got: {results}"
        )

        # The task must be in RUNNING state, attempt_count = 1.
        task = _load_task(task_id)
        assert task.status == TaskStatus.RUNNING
        assert task.attempt_count == 1

    def test_four_workers_racing_only_one_wins(self):
        """Four threads compete for one task; only one claims it."""
        _, task_id = _make_project_and_task("sleep", {"seconds": 0.01})

        results = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_atomic_claim, f"racer-{i}", task_id) for i in range(4)]
            for f in as_completed(futures):
                results.append(f.result())

        assert results.count(True) == 1, f"Expected exactly 1 winner, got: {results}"
        task = _load_task(task_id)
        assert task.attempt_count == 1


# ---------------------------------------------------------------------------
# 3. Concurrent execution tests (real subprocess workers)
# ---------------------------------------------------------------------------

def _start_worker(worker_id: str) -> tuple[subprocess.Popen, object]:
    """Start a real worker subprocess with the given worker_id."""
    backend_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    env = {
        **os.environ,
        "TEST_DATABASE_URL": os.environ["TEST_DATABASE_URL"],
        "TEST_REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/1"),
        "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
        "JWT_SECRET_KEY": os.environ.get("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-jwt-signing"),
        "ENVIRONMENT": "test",
        "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/1"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }
    import tempfile
    f = tempfile.TemporaryFile(mode="w+b")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.workers.runtime", "--worker-id", worker_id],
        cwd=backend_dir,
        env=env,
        stdout=f,
        stderr=subprocess.STDOUT,
    )
    return proc, f


def _stop_worker(worker_handle: tuple[subprocess.Popen, object], timeout: float = 5.0) -> str:
    """Terminate the worker and return its logged output."""
    proc, log_file = worker_handle
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    log_file.seek(0)
    raw = log_file.read()
    log_file.close()
    return raw.decode("utf-8", errors="replace")


class TestConcurrentExecution:
    """Tests using real subprocess workers to demonstrate process-level parallelism."""

    def test_two_workers_process_tasks_concurrently(self):
        """Two workers execute tasks in parallel, demonstrating overlapping execution.

        We submit 4 sleep tasks (0.5s each) and start 2 worker processes.
        Rather than relying exclusively on brittle wall-clock thresholds which are
        susceptible to subprocess startup and Python VM import delays on shared CI runners,
        we verify genuine distributed concurrency by asserting that at least one pair of
        tasks executed by different workers has overlapping [started_at, finished_at]
        execution intervals:
            max(start_a, start_b) < min(finish_a, finish_b)
        """
        # Create 4 sleep tasks (0.5s each).
        task_ids = []
        for _ in range(4):
            _, tid = _make_project_and_task("sleep", {"seconds": 0.5})
            _enqueue_task_id(tid)
            task_ids.append(tid)

        # Start 2 worker processes.
        workers = [_start_worker("p3-worker-1"), _start_worker("p3-worker-2")]

        try:
            # Wait for all tasks to reach a terminal state.
            for tid in task_ids:
                _wait_for_terminal(tid, timeout=25.0)
        finally:
            logs = [_stop_worker(w) for w in workers]

        # All tasks must have succeeded.
        tasks = []
        for tid in task_ids:
            task = _load_task(tid)
            assert task.status == TaskStatus.SUCCESS, (
                f"Task {tid} expected SUCCESS, got {task.status}"
            )
            tasks.append(task)

        # Check worker distribution: both workers processed work
        worker_task_map: dict[str, list[Task]] = {}
        for t in tasks:
            assert t.started_at is not None and t.finished_at is not None
            worker_task_map.setdefault(t.worker_id, []).append(t)

        assert len(worker_task_map) >= 2, (
            f"Expected tasks to be processed by at least 2 distinct workers, got: {list(worker_task_map.keys())}"
        )

        # Check for overlapping execution intervals across different workers:
        # Two tasks overlap if: max(start_a, start_b) < min(finish_a, finish_b)
        has_overlap = False
        for i, t_a in enumerate(tasks):
            for j, t_b in enumerate(tasks):
                if i < j and t_a.worker_id != t_b.worker_id:
                    overlap_start = max(t_a.started_at, t_b.started_at)
                    overlap_end = min(t_a.finished_at, t_b.finished_at)
                    if overlap_start < overlap_end:
                        has_overlap = True
                        break
            if has_overlap:
                break

        assert has_overlap, (
            "Expected at least one pair of tasks executed by different workers to have "
            f"overlapping execution intervals. Task timestamps: {[(t.id, t.worker_id, t.started_at, t.finished_at) for t in tasks]}"
        )

    def test_tasks_distributed_across_two_workers(self):
        """With 2 workers and 4 tasks, both workers should process at least one task.

        This test creates 4 tasks and verifies that BLPOP distributes them.
        Note: Redis does not guarantee strict round-robin; we only require both
        workers processed at least one task each.
        """
        # Create 4 tasks with very short sleep so workers stay alive and hungry.
        task_ids = []
        for _ in range(4):
            _, tid = _make_project_and_task("sleep", {"seconds": 0.1})
            task_ids.append(tid)

        # Enqueue all tasks before starting workers to maximise race opportunity.
        for tid in task_ids:
            _enqueue_task_id(tid)

        workers = [_start_worker("dist-worker-A"), _start_worker("dist-worker-B")]

        try:
            for tid in task_ids:
                _wait_for_terminal(tid, timeout=25.0)
        finally:
            logs = [_stop_worker(w) for w in workers]

        # All tasks reached terminal state.
        for tid in task_ids:
            assert _load_task(tid).status == TaskStatus.SUCCESS

        # Verify that both workers appear in the combined logs.
        combined_logs = "\n".join(logs)
        assert "dist-worker-A" in combined_logs, (
            "worker dist-worker-A did not appear in logs — may not have processed any task"
        )
        assert "dist-worker-B" in combined_logs, (
            "worker dist-worker-B did not appear in logs — may not have processed any task"
        )

    def test_one_task_not_executed_by_multiple_workers(self):
        """A single task must not be executed by more than one worker.

        We verify attempt_count == 1 for each task, which proves the atomic
        claim prevented duplicate execution.
        """
        task_ids = []
        for _ in range(4):
            _, tid = _make_project_and_task("sleep", {"seconds": 0.1})
            _enqueue_task_id(tid)
            task_ids.append(tid)

        workers = [_start_worker("nodup-A"), _start_worker("nodup-B")]

        try:
            for tid in task_ids:
                _wait_for_terminal(tid, timeout=25.0)
        finally:
            [_stop_worker(w) for w in workers]

        for tid in task_ids:
            task = _load_task(tid)
            assert task.attempt_count == 1, (
                f"Task {tid} has attempt_count={task.attempt_count} — "
                "expected 1 (one worker should claim each task exactly once)"
            )

    def test_worker_id_appears_in_logs(self):
        """Worker ID must appear in the worker process output."""
        _, task_id = _make_project_and_task("sleep", {"seconds": 0.05})
        _enqueue_task_id(task_id)

        w = _start_worker("logcheck-worker")
        try:
            _wait_for_terminal(task_id, timeout=20.0)
        finally:
            logs = _stop_worker(w)

        assert "logcheck-worker" in logs, (
            f"Worker ID 'logcheck-worker' not found in worker output:\n{logs[:500]}"
        )
