# ADR-001: PostgreSQL as source of truth

## Decision
Use PostgreSQL as the authoritative durable store for task, worker, workflow, lease, attempt, and event state.

## Rationale and alternatives
PostgreSQL provides transactions, relational integrity, indexed querying, and durable audit history needed to reject invalid transitions and recover after queue/worker failure. Redis-only is fast but unsuitable as the authoritative history and recovery model. MongoDB was considered but offers no advantage over PostgreSQL for this relational, transition-heavy design. In-memory state is lost on process failure and cannot support distributed recovery.

## Consequences
Every durable decision is written to PostgreSQL; Redis messages may be duplicated or lost and are reconciled from it.
