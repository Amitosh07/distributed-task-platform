# ADR-008: Phase 3 Atomic Task Claim for Multi-Worker Safety

**Status:** Accepted  
**Date:** 2026-08-15  
**Phase:** 3 — Multiple Workers + Concurrency

---

## Context

Phase 3 introduces multiple concurrent worker processes consuming from the same
Redis queue. All workers call `BLPOP task_queue` — a naturally exclusive
operation that removes each message from the queue, so in the common case only
one worker receives any given message.

However, the Phase 2 claim sequence was a non-atomic read-then-write:

```text
1. SELECT task FROM PostgreSQL WHERE id = :task_id   ← read
2. Check task.status == QUEUED                        ← in-Python check
3. UPDATE tasks SET status = 'RUNNING' ...            ← write (separate statement)
```

Between steps 1 and 3, another worker could complete an identical sequence for
the same task ID (e.g., if Redis delivers the same message twice, or via any
future re-enqueue path). This is a classic time-of-check/time-of-use (TOCTOU)
race.

---

## Decision

Replace the read-then-write sequence with a **single conditional UPDATE**:

```sql
UPDATE tasks
SET    status        = 'RUNNING',
       started_at    = :now,
       attempt_count = attempt_count + 1
WHERE  id     = :task_id
AND    status = 'QUEUED'
```

Then inspect `rowcount`:

- `rowcount == 1` → this worker is the successful claimant → proceed to handler.
- `rowcount == 0` → another worker already claimed it (or the task is not QUEUED) → skip.

PostgreSQL executes this as a single atomic statement under row-level locking.
Two concurrent workers issuing this UPDATE for the same `task_id` are serialised
by PostgreSQL: exactly one succeeds, the other gets `rowcount = 0`.

The claim is committed in its own short transaction **before** the handler runs,
so the DB connection is not held open for the entire task duration.

---

## Consequences

**Positive:**
- Exactly-once execution guarantee under concurrent workers in the common case.
- No external coordination (no distributed locks, no Redis SET NX lease).
- Short claim transaction — the DB connection is released before handler execution.
- Works with any number of workers without code changes.

**Negative / accepted:**
- If a worker crashes after a successful claim but before completing, the task
  stays in `RUNNING` indefinitely. There is no heartbeat or lease expiry in
  Phase 3. Recovery is a Phase 4 concern.
- Very high worker counts competing for a hot task will serialise at the DB
  level, but this is expected and safe behaviour.

---

## Alternatives considered

### Redis SET NX lease per task
Using `SET task:<id>:claim <worker_id> NX EX <ttl>` would provide an
in-Redis exclusive lock. Rejected because:
- It requires Redis to remain authoritative for claim state, but PostgreSQL is
  the authoritative source of truth.
- It adds TTL complexity that belongs in Phase 4 (leases / heartbeats).

### Optimistic locking with version counter
Add a `version` column and include `AND version = :expected_version` in the
UPDATE. Rejected for Phase 3 — adds schema complexity with no benefit over the
simpler `WHERE status = 'QUEUED'` guard.

---

## Testing

The atomic claim is verified by `TestAtomicClaim.test_two_workers_racing_only_one_wins`
in `test_phase3_concurrency.py`, which spawns two threads that simultaneously
call `_atomic_claim()` for the same task ID and asserts exactly one returns
`True`.
