# ADR-010: Phase 5 Workflow DAGs, Dependencies & Parallel Branches

**Status:** Accepted  
**Date:** 2026-08-15  
**Phase:** 5 — Workflow DAGs, Dependencies & Parallel Branches

---

## Context

Phases 1–4 provided a reliable single-task execution platform featuring multi-worker concurrency, heartbeat/lease management, timeout enforcement, exponential backoff retries, and failure recovery. However, complex real-world workloads require multi-step pipelines with dependencies (e.g. Extract -> Transform -> Load), parallel branch execution (fan-out / fan-in), and customizable failure handling policies.

---

## Decision

We have designed and implemented a directed acyclic graph (DAG) workflow orchestration engine with the following core architectural decisions:

1. **Separation of Definition vs. Runtime Execution State**:
   - **`workflows`**, **`workflow_nodes`**, and **`workflow_edges`** define the reusable, immutable DAG structure.
   - **`workflow_runs`** records an individual execution of a workflow.
   - **`workflow_run_nodes`** records the per-run execution state (status, task reference, start/finish times, errors) for each node.
   - Database constraint `UNIQUE(workflow_run_id, workflow_node_id)` guarantees complete run isolation across concurrent executions.

2. **Graph Validation & Cycle Detection (Kahn's Algorithm)**:
   - Workflow definitions are structurally validated at creation time (`validate_workflow`).
   - Kahn's algorithm performs topological sorting to detect and reject cycles before persisting to PostgreSQL.
   - Self-loops, duplicate edges, missing node references, unsupported task types, and missing payload fields are rejected with 422 errors.

3. **Orchestration via Existing Task Machinery (No Handler Bypassing)**:
   - The workflow engine does **NOT** execute handlers directly.
   - Architecture follows: `Workflow API -> Workflow Engine -> Task records in PostgreSQL -> Redis -> Existing Workers -> Task Handlers`.
   - Workflow nodes specify task types, payloads, timeouts, and max retries, which are converted into standard Phase 1–4 `Task` records.
   - Phase 4 remains the single source of truth for heartbeats, leases, timeouts, exponential backoff, and failure recovery.

4. **Atomic Duplicate-Dispatch Prevention**:
   - Node dispatch uses conditional atomic SQL updates (`UPDATE workflow_run_nodes SET status='RUNNING', started_at=:now WHERE id=:id AND status='PENDING'`).
   - If multiple workers or advancing threads attempt to dispatch the same ready node simultaneously, exactly one claimant succeeds (`rowcount = 1`), preventing duplicate `Task` generation.

5. **Dependency Evaluation & Intermediate Failure Protection**:
   - A node transitions to `READY`/`RUNNING` only when **ALL** predecessor nodes in the DAG have reached final `SUCCESS`.
   - Intermediate retry failures (`TaskStatus.QUEUED` during retry backoff) do not unlock downstream nodes.
   - Downstream nodes remain `PENDING` until the retrying predecessor finishes with final `SUCCESS`.

6. **Failure Policies (`FAIL_FAST` vs. `CONTINUE`)**:
   - **`FAIL_FAST`**: Upon any node failure, all remaining `PENDING` nodes in the run are immediately transitioned to `SKIPPED`, and the workflow run transitions to `FAILED`.
   - **`CONTINUE`**: When a node fails, downstream dependent nodes are marked `SKIPPED`, but independent parallel branches continue running to completion.
   - Cascading skip propagation ensures that any node downstream of a `SKIPPED` or `FAILED` node is also recursively marked `SKIPPED`.

7. **Worker Runtime Hook**:
   - Worker runtime `_process_task` invokes a lightweight hook `_try_advance_workflow(task_id)` upon reaching terminal task states (`SUCCESS` / `FAILED`).
   - Standalone tasks (where `workflow_run_node_id is None`) incur zero overhead.

---

## Consequences

**Positive:**
- Complex multi-step pipelines and parallel fan-out / fan-in DAGs run with deterministic dependency guarantees.
- Seamless reuse of battle-tested Phase 1–4 reliability, lease, timeout, retry, and recovery mechanisms without code duplication.
- High scalability: independent ready nodes are distributed across available worker processes automatically via Redis.
- Comprehensive auditability: every workflow run and node transition is persisted in PostgreSQL.

**Negative / Trade-offs:**
- Graph structure is fixed at workflow definition time (static DAGs); dynamic runtime branching or loop constructs require future extension.
