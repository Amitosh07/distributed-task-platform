# ADR-009: Phase 4 Worker Heartbeats, Task Leases, Timeouts & Failure Recovery

**Status:** Accepted  
**Date:** 2026-08-15  
**Phase:** 4 — Worker Heartbeats, Task Leases, Timeouts, Retries & Failure Recovery

---

## Context

Phase 3 introduced concurrent workers consuming from a shared Redis `task_queue` with atomic `QUEUED → RUNNING` claims. However, if a worker crashed or experienced a network partition after claiming a task, the task would remain stuck in `RUNNING` status indefinitely. Furthermore, long-running tasks had no execution timeout enforcement, and failing tasks had no retry or backoff policies.

---

## Decision

We have implemented a comprehensive reliability model covering worker heartbeats, task leases, timeout enforcement, exponential backoff retries, and concurrency-safe stale task recovery:

1. **Worker Liveness & Registry**:
   - Each worker registers in the `workers` table (`status = 'ACTIVE'`, `started_at`, `last_heartbeat_at`).
   - A background maintenance thread issues heartbeats every `HEARTBEAT_INTERVAL_SECONDS` (default: 2.0s).
   - Workers with no heartbeat within `WORKER_STALE_THRESHOLD_SECONDS` (default: 10.0s) are marked `STALE`.
   - Clean shutdown (SIGTERM/SIGINT) marks the worker `STOPPED`.

2. **Task Leases & Ownership**:
   - Claiming a task sets `worker_id`, `lease_acquired_at`, and `lease_expires_at = now + TASK_LEASE_SECONDS` (default: 10.0s).
   - The worker's background thread renews active task leases every `TASK_LEASE_RENEW_INTERVAL_SECONDS` (default: 3.0s) via conditional update (`WHERE id=:task_id AND status='RUNNING' AND worker_id=:worker_id`), preventing imposter workers from renewing other workers' leases.

3. **Stale Task Detection & Concurrency-Safe Recovery**:
   - Stale tasks (`status = 'RUNNING' AND lease_expires_at < now`) are periodically detected and recovered via an atomic conditional SQL update.
   - If attempts remain (`attempt_count <= max_retries`), the task transitions to `QUEUED`, clears `worker_id` and lease fields, and re-publishes the task ID to Redis.
   - If attempts are exhausted (`attempt_count > max_retries`), the task transitions to `FAILED`.
   - The atomic update guarantees that two recovery processes racing for the same task result in exactly one successful claimant.

4. **Task Execution Timeouts & Worker Survival**:
   - Handlers are executed within a bounded thread pool with timeout enforcement (`timeout_seconds`, max 86400s).
   - If a handler exceeds its configured timeout, a `TaskTimeoutError` is raised and caught.
   - The worker process survives and moves the timed-out task into the retry/failure flow.

5. **Retry Policy & Backoff**:
   - Bounded retries: `max_retries` represents additional retry attempts allowed after the initial execution attempt (total attempts = `1 + max_retries`).
   - Non-retryable errors (`NonRetryableError`, `ValueError`, schema errors) fail immediately without retry.
   - Retryable errors (including `TaskTimeoutError` and transient runtime errors) calculate exponential backoff: `min(max_seconds, base_seconds * 2^(attempt - 1))`, requeue in PostgreSQL, and re-publish to Redis.

6. **Race Condition Protection**:
   - **Success vs Recovery Race**: Late worker task completion is guarded by `WHERE worker_id = :worker_id`. If recovery already claimed the task, the late completion returns 0 rows and is safely discarded.
   - **Timeout vs Success Race**: Terminal transitions are protected so that terminal states (`SUCCESS` / `FAILED`) are never overwritten by late timeouts or recovery scans.

---

## Consequences

**Positive:**
- Complete fault tolerance: crashed workers, expired leases, and hung tasks automatically self-heal and complete on healthy workers.
- True at-least-once execution guarantee with ownership and lease protection.
- Worker processes never crash from individual task timeouts or handler errors.
- Clean separation between worker liveness (`Worker` heartbeats) and task ownership (`Task` leases).

**Negative / Trade-offs:**
- In the event of a network partition (where Worker A is partitioned but still computing), Worker B may recover the task, leading to duplicate execution of the handler. Exactly-once execution is not possible across network partitions; handlers must remain idempotent where side-effects occur.
