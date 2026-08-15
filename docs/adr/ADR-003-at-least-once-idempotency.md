# ADR-003: At-least-once delivery with idempotency

## Decision
Use at-least-once delivery and require idempotency; do not claim exactly-once execution.

## Rationale
Worker failure after a side effect but before durable acknowledgement, lease expiry, and duplicate queue delivery can cause duplicate execution. Exactly-once across workers, databases, queues, and arbitrary external systems is not claimed. Submission idempotency keys deduplicate a logical request within a project; task IDs and handler operation IDs enable handlers/downstream systems to detect repeats.

## Consequences
Handlers must define safe duplicate behavior. Attempts/events expose duplicates and stale reports are rejected with attempt/lease tokens.
