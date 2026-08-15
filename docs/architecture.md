# Architecture

## 1. System overview

The platform is initially a modular monolith: one FastAPI application owns the HTTP/API and domain rules, with separately running worker processes for asynchronous execution. PostgreSQL is the authoritative durable source of truth; Redis is the fast queue and coordination layer. A React dashboard calls the API and never coordinates workers directly.

**Phase 4 (current):** Multiple independent worker processes with active registration and heartbeats; task leases with periodic renewal; execution timeout enforcement; retry policies with exponential backoff; concurrency-safe stale task recovery.

```text
Browser
  |
  v
React Dashboard
  |
  v
FastAPI API -------------------------------------------------+
  |                                                         |
  | authentication, validation, task/workflow/worker APIs  |
  v                                                         |
PostgreSQL <--------------------> Redis                     |
(durable state and history)       (queue and coordination)  |
                                      |                      |
                         +------------+------------+         |
                         |            |            |         |
                         v            v            v         |
                      Worker 1     Worker 2     Worker N -----+
                         | (heartbeats & leases)   |
                         +------------+------------+
                                      |
                                      v
                               Task execution

Prometheus collects metrics from the API, Redis integration, and workers.
Grafana visualizes those metrics. OpenTelemetry correlates requests and work.
```

**Principle: PostgreSQL is the source of truth; Redis is the queue/coordination layer.** A Redis loss or an expired queue item must be recoverable from PostgreSQL. Large results and authoritative state are not stored in Redis.

## 2. Component responsibilities

| Component | Responsibility and reason |
|---|---|
| React frontend | Authenticated dashboard for task, workflow, worker, log, and health visibility. It makes asynchronous work understandable to operators and clients. |
| FastAPI API | Authentication, project authorization, input validation, durable submission, monitoring, workflow definition/execution, and worker-management contracts. |
| PostgreSQL | Transactional, durable record of users, projects, tasks, attempts, events, workers, leases, workflows, and runs. It is used to recover or reconcile queue state. |
| Redis | Low-latency dispatch queues, short-lived coordination, and temporary scheduling/claim signals. It never replaces durable task state. |
| Scheduler | Future design component that finds due `scheduled_at` tasks and makes them queueable; recurring schedules are out of V1 scope. |
| Worker runtime | Independently running processes that register, claim work with leases, execute with timeouts, renew leases, report heartbeats, retry failures with backoff, and recover stale work. |
| Workflow engine | Validates DAG definitions, determines ready nodes from persisted dependencies, and creates/coordinates task work for workflow runs. |
| Prometheus | Scrapes reproducible metrics from API and workers; no performance values are invented. |
| Grafana | Displays operational and benchmark dashboards using Prometheus data. |
| OpenTelemetry | Carries trace context from API request through dispatch and worker execution; structured logs include correlation IDs. |
| Docker | Later provides repeatable local and VM deployment packaging; it is not configured in this phase. |

## 3. Data flow and task submission

### Phase 4 pipeline

```text
Client → POST /v1/tasks
  ↓
FastAPI: validate request, authenticate, check project access
  ↓
PostgreSQL: persist task (status = QUEUED, queued_at = now)
  ↓
Redis: RPUSH task_queue {"task_id": "<UUID>"}
  ↓ (API returns immediately — response does not wait for execution)
Workers (N processes running python -m app.workers.runtime --worker-id worker-N):
  1. Register in PostgreSQL as ACTIVE and maintain periodic heartbeats via background thread.
  2. Consume from shared task_queue via BLPOP.
  3. Atomic DB claim with expiring lease:
       UPDATE tasks SET status='RUNNING', worker_id=:worker_id, lease_acquired_at=now,
                        lease_expires_at=now + lease_duration, attempt_count=attempt_count+1
       WHERE id=:task_id AND status='QUEUED'
  4. While executing handler, background thread periodically renews lease.
  5. Handler executes inside bounded thread pool with timeout enforcement (timeout_seconds).
  6. Outcome handling:
       - Success: RUNNING → SUCCESS (only if worker_id still matches, guarding against late completion after recovery).
       - Retryable Error / Timeout (attempts <= max_retries): calculate exponential backoff, requeue in PostgreSQL as QUEUED, re-publish to Redis.
       - Non-Retryable Error / Retries Exhausted: RUNNING → FAILED.
  7. Background maintenance scanner detects stale tasks (RUNNING with expired leases) and recovers them back to QUEUED.
```

The API validates the task type, handler payload, project access, priority, timeout, scheduling, retry policy, and idempotency key. It persists the task before publishing its ID to Redis. A client receives a task ID rather than waiting for execution.

### Database/Redis consistency and reconciliation (Phase 4)

The durability boundary is PostgreSQL (ADR-001, ADR-007, ADR-008, ADR-009):

1. Task is persisted in PostgreSQL as `QUEUED` in a committed transaction.
2. Task ID is then published to Redis.
3. If Redis publish fails after the DB commit, the task remains durably `QUEUED` in PostgreSQL.
4. If a worker crashes or drops off during execution, its lease expires (`lease_expires_at < now`). The background recovery service detects the stale task, resets it to `QUEUED`, and re-enqueues it on Redis.
5. All task state transitions are conditional on `status` and `worker_id`, guaranteeing concurrency safety across racing workers and recovery scanners.


## 4. Execution, registration, heartbeat, and recovery flows

### Task execution

1. A worker receives a task reference and reads/claims the task using an atomic PostgreSQL state/lease check.
2. It creates a `task_attempt`, records the worker ID, transitions the task to `RUNNING`, and persists an event.
3. It runs a fixed, validated handler subject to the task timeout.
4. It durably records result/error and transitions to `SUCCESS`, `RETRY_WAIT`, `FAILED`, `DEAD_LETTER`, or `TIMED_OUT`; it releases the lease. Redis acknowledgements are secondary to this durable outcome.

### Worker registration and heartbeat

A worker registers with its ID, hostname, version, capabilities, and concurrency limit. The API persists that record and a registration event. Every few seconds it sends a heartbeat with health/capacity information; the API persists `last_heartbeat_at` and status. While owning a task, the worker also renews that task's lease before expiry. Heartbeats describe worker liveness; leases protect individual tasks.

### Failure recovery

A recovery process identifies unhealthy workers and expired task leases from PostgreSQL. It records recovery events and requeues eligible work or finalizes it according to timeout/retry policy. Completion reports must carry the active attempt/lease token; reports from an expired or superseded lease are rejected as stale. This preserves at-least-once delivery and makes recovery auditable.

## 5. Workflow/DAG execution

Workflow definitions persist nodes and directed edges. Creation validates that the graph is acyclic. At run time the workflow engine creates a `workflow_run`; a node becomes `READY` only after all required predecessors succeeded. Independent ready nodes may be submitted concurrently. Later configuration will choose fail-fast or continue behavior for dependency failure; the run and causal node results remain durable in either case.

## 6. Observability and security boundaries

Prometheus will expose counters (`tasks_submitted_total`, success/failure/retry/worker-failure totals), histograms (`task_queue_wait_seconds`, `task_execution_seconds`, API/workflow duration), and gauges (queue depth, active workers, running tasks, available slots). Queue wait is measured from `queued_at` to the successful `RUNNING` claim; execution time is from `started_at` to `finished_at`. OpenTelemetry links API, queue-dispatch, and worker spans. Grafana is visualization only.

The browser is an untrusted client. The API authenticates users/API keys, applies RBAC and project-level authorization, limits payloads, validates schemas, and protects secrets. Workers only execute registered safe handlers; untrusted code is not accepted. PostgreSQL and Redis are private service boundaries. Production later adds TLS, secret management, rate limits, non-root containers, and resource limits.

## 7. Database schema design

| Table | Purpose, key columns, relationships, and indexes |
|---|---|
| `users` | Users. PK `id`; `email` (unique), `password_hash`, `role`, `created_at`. Owns projects and API keys; unique email index. Passwords are hashed, never plaintext. |
| `projects` | Authorization/ownership scope. PK `id`; FK `owner_id -> users`, `name`, `status`, `created_at`. Index `owner_id`; unique owner/name if product rules require it. |
| `api_keys` | Machine credentials. PK `id`; FK `user_id -> users`, `key_hash`, `name`, `last_used_at`, `revoked_at`, `created_at`. Unique key-hash and `user_id` indexes. |
| `tasks` | Authoritative task record. PK `id`; FK `project_id -> projects`; `type`, `payload_json`, `status`, `priority`, `idempotency_key`, `scheduled_at`, `timeout_seconds`, `max_retries`, `attempt_count`, `created_at`, `queued_at`, `started_at`, `finished_at`, result/error summary. Indexes: `(project_id,status)` for project views; `(status,priority,created_at)` for dispatch/queue ordering; `(scheduled_at)` for scheduler scans; unique `(project_id,idempotency_key)` when supplied. |
| `task_attempts` | One execution attempt. PK `id`; FKs `task_id -> tasks`, nullable `worker_id -> workers`; `attempt_no`, `status`, `started_at`, `finished_at`, `error_code`, `error_message`, result metadata, lease token. Unique `(task_id,attempt_no)` and index `(task_id)`. |
| `task_events` | Immutable task timeline/audit. PK `id`; FK `task_id -> tasks`; `event_type`, `actor_id`/`worker_id`, `metadata_json`, `created_at`. Index `(task_id,created_at)`. |
| `workers` | Worker registry/liveness. PK `id`; `hostname`, `version`, `status`, `capabilities_json`, `concurrency_limit`, `last_heartbeat_at`, `registered_at`. Index `(status,last_heartbeat_at)` for health scans. |
| `task_leases` | Current per-task ownership. PK/unique `task_id`; FKs `task_id -> tasks`, `worker_id -> workers`; `attempt_id`, `lease_token`, `acquired_at`, `expires_at`. Index `(expires_at)` for recovery and `(worker_id)` for worker inspection. |
| `workflows` | DAG definition. PK `id`; FK `project_id -> projects`; `name`, definition status, `created_at`. Index `project_id`. |
| `workflow_nodes` | Nodes in a workflow. PK `id`; FK `workflow_id -> workflows`; `node_key`, `task_type`, `payload_json`, definition status. Unique `(workflow_id,node_key)` and PRD index `(workflow_id,status)`. |
| `workflow_edges` | Directed dependencies. Composite PK `(workflow_id,from_node_id,to_node_id)`; FKs to workflow/nodes. Index `(workflow_id,to_node_id)` for prerequisite evaluation. |
| `workflow_runs` | Execution instance. PK `id`; FK `workflow_id -> workflows`; `status`, `started_at`, `finished_at`, policy/result/error metadata. Index `(workflow_id,started_at)`. A later node-run mapping may associate nodes/tasks with a run without changing the durable-source principle. |

Indexes are initial designs; additional indexes require measured query plans (`EXPLAIN ANALYZE`).

## 8. Deployment architecture

Local development will later run the frontend, API, worker processes, PostgreSQL, Redis, Prometheus, and Grafana with Docker Compose. Developers may also run API/workers directly against local services, but state remains in PostgreSQL. No services are created in Phase 0.

After the local system is tested, cloud V1 targets a simple Linux VM running the API/workers and Compose behind Caddy or Nginx with HTTPS, backups, monitoring, and secrets. Managed PostgreSQL and Redis are optional. Kubernetes, Kafka, and additional microservices are deliberately excluded unless measured requirements justify them.

## 9. Design constraints

- PostgreSQL is durable authority; Redis is queue/coordination only.
- Delivery is at least once; idempotency is required and workers can fail.
- Tasks are asynchronous, recoverable, and eventually concurrent across workers.
- Workflows are acyclic DAGs; cyclic workflows are invalid.
- Public arbitrary code execution is prohibited in V1; large results are not kept in Redis.
- No initial Kubernetes, Kafka, or unnecessary microservices.
- Metrics and benchmark claims must be reproducible and never invented.
