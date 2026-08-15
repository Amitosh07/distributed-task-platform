# Task State Machine and Failure Model

## Lifecycle

```text
CREATED -> QUEUED -> RUNNING -> SUCCESS
    |          |        |  \-> RETRY_WAIT -> QUEUED
    |          |        |  \-> FAILED
    |          |        |  \-> DEAD_LETTER
    |          |        |  \-> TIMED_OUT
    |          \------> CANCELLED
    \-----------------> CANCELLED
```

Only the transitions below are valid; conditional database updates reject every other transition. Every transition writes a timestamped `task_event`; changes to the task state and event are persisted transactionally. The task tracks attempt count, worker ID through attempts/events, start/end timestamps, and structured error information. The API/dashboard will eventually expose the resulting event timeline.

| Transition | Cause/actor | Persisted change | Retry/event |
|---|---|---|---|
| `CREATED -> QUEUED` | API after validation/persistence, scheduler when `scheduled_at` becomes due, or recovery reconciler | `queued_at`, status, queue eligibility | Publish to Redis may retry; record `queued`. |
| `QUEUED -> RUNNING` | Worker that atomically claims eligible task and lease | worker/attempt, lease token/expiry, `attempt_count`, `started_at` when first run | Claim conflict is retryable by another worker; record `started`. |
| `RUNNING -> SUCCESS` | Current lease holder reports valid successful handler outcome | result metadata, attempt end, `finished_at`, lease release | Not retried automatically; record `succeeded`. |
| `RUNNING -> RETRY_WAIT` | Current worker reports retryable failure, or recovery determines retryable interrupted attempt | error, attempt end, calculated next eligible time, lease release | Yes, after backoff/jitter; record `retry_scheduled`. |
| `RETRY_WAIT -> QUEUED` | Scheduler/reconciler after retry time | status and `queued_at`/dispatch eligibility | Enqueue may retry; record `requeued`. |
| `RUNNING -> FAILED` | Non-retryable handler failure, or retry policy ends without dead-letter policy | error, attempt end, `finished_at`, release lease | No automatic retry; record `failed`. |
| `RUNNING -> DEAD_LETTER` | Retryable failure or recovery after attempts are exhausted | final error, attempt end, `finished_at`, release lease | Manual/operator retry is a separate future action; record `dead_lettered`. |
| `RUNNING -> TIMED_OUT` | Timeout monitor/current worker detects timeout and safely terminates or marks work expired | timeout error, attempt end, `finished_at`, release/expire lease | Future policy may retry if safe and attempts remain; record `timed_out`. |
| `CREATED -> CANCELLED` | Authorized client cancels before queue eligibility | `finished_at`, cancellation metadata | No automatic retry; record `cancelled`. |
| `QUEUED -> CANCELLED` | Authorized client cancels before successful claim | `finished_at`, cancellation metadata; stale queue references become no-ops | No automatic retry; record `cancelled`. |

Manual retry is not a bypass of the model: it validates an eligible terminal state, creates an auditable retry request, and returns the task to queue eligibility under a defined policy. Late worker completions are rejected if their lease/attempt token is no longer current.

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

`A` must succeed before `B` and `C` are `READY`; `B` and `C` may run concurrently; `D` is `READY` only after both succeed. Workflow creation validates a directed acyclic graph and rejects cycles. The workflow engine persists run/node progress and evaluates dependencies from durable state. A later policy will choose fail-fast or continue behavior after a node failure; neither policy permits dependent work to run without its declared requirements being satisfied.
