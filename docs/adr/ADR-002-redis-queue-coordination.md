# ADR-002: Redis for queueing and coordination

## Decision
Use Redis for fast task dispatch and short-lived worker/task coordination.

## Rationale
Redis efficiently supports low-latency queue operations and ephemeral coordination signals. It is not the durable source of truth: it does not own task lifecycle/history, results, workflow state, or recovery authority. The database-first submission and reconciliation process repairs missing queue references; workers validate durable state before executing.

## Consequences
Redis outages degrade dispatch but do not erase authoritative work. Large results remain in PostgreSQL or later durable object storage, never Redis.
