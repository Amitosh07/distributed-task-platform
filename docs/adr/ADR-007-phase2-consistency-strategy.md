# ADR-007: Phase 2 PostgreSQL/Redis Consistency Strategy

**Status:** Accepted  
**Date:** 2026-08-15  
**Phase:** 2 — Redis Queue + One Worker

---

## Context

Phase 2 introduces Redis as the task dispatch queue. Every task creation now
involves two writes to two different systems:

1. **PostgreSQL** — authoritative durable record (task state = QUEUED)
2. **Redis** — task ID published to `task_queue` for worker pickup

These two writes cannot be made atomically without a distributed transaction
coordinator. We must decide what to do when one write succeeds and the other
fails.

---

## Decision

**PostgreSQL is written first. Redis is published after.**

Concretely, `create_task()` in Phase 2:

1. Persists the task in PostgreSQL with status `QUEUED` and sets `queued_at`.
2. Commits the PostgreSQL transaction.
3. Calls `publish_task(task_id)` to push the task ID to the Redis list `task_queue`.
4. Returns the API response immediately.

### Failure modes and Phase 2 behaviour

| Scenario | Outcome |
|---|---|
| PostgreSQL succeeds, Redis publish succeeds | Normal path — task reaches worker |
| PostgreSQL succeeds, Redis publish **fails** | Task stays `QUEUED` in PostgreSQL (durable). A warning is logged. The API still returns the task. A future reconciler (Phase 4) will re-enqueue stranded `QUEUED` tasks that have no active worker claim. |
| PostgreSQL **fails**, Redis publish never attempted | Task never created. API returns an error. No queue pollution. |
| API crashes after PostgreSQL commit but before Redis publish | Same as "PostgreSQL succeeds, Redis fails" — task is `QUEUED` in PostgreSQL and will be recovered by the Phase 4 reconciler. |
| Worker receives message but task is in a terminal state (duplicate) | Worker skips execution (defensive check in `_process_task`). |

### Why NOT the full transactional outbox in Phase 2

The transactional outbox pattern (persist an "outbox" record in the same
PostgreSQL transaction, then a separate relay process reads and publishes)
guarantees exactly-once publish but adds significant complexity:

- A relay process reading from the outbox table
- Additional DB table and indexing
- Relay polling or change-data-capture integration

The PRD explicitly scopes this to Phase 4 ("Reliability — retries, recovery,
idempotency"). Phase 2 only requires "a simple design consistent with the PRD"
and "document the chosen Phase 2 behaviour."

At-least-once delivery plus idempotency is the documented architecture
(ADR-003). A QUEUED task that is not picked up by a worker is recoverable
from PostgreSQL; no task state is lost.

---

## Consequences

**Positive:**
- Simple implementation — no new tables, no relay process.
- PostgreSQL remains the authoritative source of truth at all times.
- Redis failure does not corrupt durable state.
- Failure mode is observable (logged warning) and recoverable.

**Negative / accepted risk:**
- A task can be `QUEUED` in PostgreSQL but not yet in the Redis queue (stranded).
  In Phase 2 this requires a manual re-enqueue or a server restart (which will
  not re-enqueue automatically). Phase 4 adds the reconciler.
- A Redis publish failure after a successful PostgreSQL commit is not
  automatically retried in Phase 2 (Phase 4 scope).

**No risk of data corruption:**
- A task is never marked `SUCCESS` based only on a Redis operation.
- All terminal state transitions are persisted in PostgreSQL by the worker.
- Redis is used only as a dispatch signal, not as authoritative state.
