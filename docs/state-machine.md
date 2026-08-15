# Task State Machine and Failure Model

## Lifecycle

```text
CREATED -> QUEUED -> RUNNING -> SUCCESS
    |          |        |  \-> RETRY_WAIT -> QUEUED   [Phase 4]
    |          |        |  \-> FAILED
    |          |        |  \-> DEAD_LETTER             [Phase 4]
    |          |        |  \-> TIMED_OUT               [Phase 4]
    |          \------> CANCELLED                      [Phase 3+]
    \-----------------> CANCELLED                      [Phase 3+]
```

## Phase 4 active transitions

Phase 4 implements and validates full reliability lifecycle transitions with leases, retries, timeouts, and stale task recovery.

| Transition | Actor | Persisted change | Concurrency & Reliability guarantee |
|---|---|---|---|
| `CREATED → QUEUED` | API (`create_task`) during submission | `queued_at`, `status = QUEUED` committed before Redis publish | DB unique constraint on idempotency key prevents duplicate submission |
| `QUEUED → RUNNING` | Worker (`_atomic_claim`) on message receipt | `started_at`, `worker_id`, `lease_acquired_at`, `lease_expires_at`, `attempt_count += 1`, `status = RUNNING` | Conditional `UPDATE ... WHERE status = 'QUEUED'` guarantees single claimant and issues expiring lease |
| `RUNNING → SUCCESS` | Worker after handler completes | `result_summary`, `finished_at`, `status = SUCCESS`, committed | Guarded by `WHERE worker_id = :worker_id`; late completion after recovery is safely ignored |
| `RUNNING → QUEUED` (Retry / Requeue) | Worker on retryable error or timeout (attempts <= max_retries) | `status = QUEUED`, clears `worker_id` and lease fields, sets `error_message`, re-publishes to Redis | Bounded exponential backoff applied before re-enqueueing |
| `RUNNING → QUEUED` (Stale Recovery) | Background recovery scanner when `lease_expires_at < now` (attempts <= max_retries) | `status = QUEUED`, clears `worker_id` and lease fields, sets recovery error, re-publishes to Redis | Atomic conditional update `WHERE status='RUNNING' AND lease_expires_at < :now` |
| `RUNNING → FAILED` | Worker on non-retryable error or exhausted retries; or recovery when retries exhausted | `error_message`, `finished_at`, `status = FAILED` | Terminal failure state persisted in PostgreSQL |

### Phase 4 notes

- **Task Leases**: Every claimed task holds an expiring lease (`lease_expires_at`). The owning worker renews this lease periodically via a background thread while executing the handler.
- **Task Timeouts**: Handlers execute with timeout enforcement (`timeout_seconds`). Exceeding timeout raises `TaskTimeoutError` and enters the retry path without crashing the worker process.
- **Worker Crash Recovery**: If a worker abruptly crashes, its lease expires, and any healthy worker automatically recovers the task, resets it to `QUEUED`, and re-enqueues it on Redis.
- **At-least-once Delivery**: Handlers should remain idempotent where external side effects occur.
- **No cancellation**: `CANCELLED` is Phase 5+.


## Queue wait versus execution time

- **Queue wait time:** the durable time from `queued_at` to the successful `RUNNING` claim. It measures dispatch/capacity delay.
- **Execution time:** the time from `started_at` to `finished_at` for an attempt/task. It measures handler duration.

They are stored/calculated separately and exposed as separate metrics; combining them would conceal whether slow completion comes from capacity or execution.

## Lease and worker-failure model

```text
QUEUED
  |
  v
Worker atomically claims task + lease
  |
  v
RUNNING -- worker heartbeat and lease renewal continue
  |
  +-- worker fails / partition / stalls
          |
          v
  heartbeat becomes stale; task lease expires
          |
          v
  recovery records interruption and evaluates retry policy
          |
          v
  RETRY_WAIT or QUEUED -> another worker claims -> RUNNING
```

A heartbeat alone only says a worker was recently reachable; it does not prove that a specific task is still owned or progressing. A lease/visibility timeout is an expiring, task-specific ownership claim. It bounds how long a crashed worker can prevent recovery. The recovery process uses durable lease expiry, not Redis alone. A worker that resumes after expiry may still perform a duplicate external action, which is why delivery is at least once and handlers must be idempotent. Its stale completion is rejected via the lease/attempt token.

## Workflow/DAG model

```text
       A
      / \
     B   C
      \ /
       D
```

### Workflow Run Lifecycle (Phase 5)

```text
PENDING -> RUNNING -> SUCCESS
                \---> FAILED
```

### Workflow Run Node Lifecycle (Phase 5)

```text
PENDING -> RUNNING -> SUCCESS
    |        \------> FAILED
    \---------------> SKIPPED (dependency failed/skipped or fail-fast)
```

| State | Description |
|---|---|
| `PENDING` | Node is waiting for one or more predecessor dependencies to reach final `SUCCESS`. |
| `RUNNING` | All dependencies reached `SUCCESS`; atomic dispatch succeeded; underlying `Task` is queued/executing. |
| `SUCCESS` | Underlying `Task` completed with `TaskStatus.SUCCESS`. Prerequisite unlocked for downstream nodes. |
| `FAILED` | Underlying `Task` reached terminal failure (`FAILED`, `DEAD_LETTER`, `TIMED_OUT`). |
| `SKIPPED` | Node was bypassed because an upstream dependency failed/was skipped, or because `FAIL_FAST` triggered. |

### Failure Policy Rules

1. **`FAIL_FAST`**: When any node in the run enters `FAILED`:
   - All remaining `PENDING` nodes are immediately marked `SKIPPED`.
   - The workflow run is immediately marked `FAILED`.
   - Any currently `RUNNING` nodes complete their existing attempts, but no new work is dispatched.

2. **`CONTINUE`**: When a node enters `FAILED`:
   - Direct and indirect downstream dependent nodes are marked `SKIPPED` via cascading skip evaluation.
   - Independent parallel branches continue executing normally to completion.
   - The workflow run transitions to `FAILED` once all active branches finish.

3. **Intermediate Retries**:
   - If a task experiences a retryable error or timeout, the task re-enters `QUEUED` with exponential backoff.
   - The workflow node remains `RUNNING`.
   - Downstream dependent nodes remain `PENDING` and are **never** unlocked during retry attempts.
   - Only final `SUCCESS` unlocks downstream dependencies.
