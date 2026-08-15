# ADR-004: Worker heartbeats and task leases

## Decision
Workers send heartbeats and hold expiring task leases/visibility timeouts.

## Rationale
Heartbeats detect worker-level liveness but cannot prove ownership of a specific task. A durable lease records worker, task, attempt token, acquisition, and expiry, allowing recovery after crash/partition/stall. Workers renew leases while running; recovery requeues eligible expired work. Completion from an expired/superseded lease is rejected as stale.

## Consequences
Failure detection is bounded by heartbeat/lease settings and duplicate execution remains possible, so idempotency is mandatory.
