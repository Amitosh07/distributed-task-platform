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

## Phase 2 active transitions

Phase 2 implements and tests the following transitions. All others are defined
in the state machine but not yet triggered by application code.

| Transition | Actor | Persisted change |
|---|---|---|
| `CREATED → QUEUED` | API (`create_task`) during task submission | `queued_at`, `status = QUEUED` committed before Redis publish |
| `QUEUED → RUNNING` | Worker (`_process_task`) on message receipt | `started_at`, `attempt_count += 1`, `status = RUNNING`, committed |
| `RUNNING → SUCCESS` | Worker after handler returns successfully | `result_summary`, `finished_at`, `status = SUCCESS`, committed |
| `RUNNING → FAILED` | Worker after handler raises any exception | `error_message`, `finished_at`, `status = FAILED`, committed |

### Phase 2 notes

- **CREATED is transient in Phase 2**: tasks move directly from `CREATED` to
  `QUEUED` in a single DB commit during `create_task()`. A client never observes
  a task in `CREATED` status from the API.
- **No retries**: failed tasks stay `FAILED`. `RETRY_WAIT` and `DEAD_LETTER`
  are Phase 4 features.
- **No cancellation**: `CANCELLED` is Phase 3+.
- **No timeouts**: `TIMED_OUT` is Phase 4.
- **No leases or heartbeats**: Phase 3.
- **Worker resilience**: the worker loop catches all handler exceptions and
  continues processing the next task after a `FAILED` transition.

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
