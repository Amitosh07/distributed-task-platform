# Architecture

## 1. System overview

The platform is initially a modular monolith: one FastAPI application owns the HTTP/API and domain rules, with separately running worker processes for asynchronous execution. PostgreSQL is the authoritative durable source of truth; Redis is the fast queue and coordination layer. A React dashboard calls the API and never coordinates workers directly.

**Phase 5 (current):** Directed Acyclic Graph (DAG) workflow engine; reusable workflow definitions with Kahn's algorithm cycle detection; parallel branch execution across multiple workers; atomic duplicate-dispatch prevention; fail-fast and continue failure policies; run node state isolation via `workflow_run_nodes`.

```text
Browser
  |
  v
React Dashboard
  |
  v
FastAPI API -------------------------------------------------+
  |                                                         |
  | authentication, validation, workflows, tasks, workers   |
  v                                                         |
Workflow Engine                                             |
  | (DAG validation, dependencies, ready node dispatch)     |
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
                                      |
                                      v
                         _try_advance_workflow hook
                                      |
                                      v
                         Dependency evaluation loop
```

Prometheus collects metrics from the API, Redis integration, and workers.
Grafana visualizes those metrics. OpenTelemetry correlates requests and work.
```

**Principle: PostgreSQL is the source of truth; Redis is the queue/coordination layer.** A Redis loss or an expired queue item must be recoverable from PostgreSQL. Large results and authoritative state are not stored in Redis.

## 2. Component responsibilities

| Component | Responsibility and reason |
|---|---|
| React frontend | Authenticated dashboard for task, workflow, worker, log, and health visibility. It makes asynchronous work understandable to operators and clients. |
| FastAPI API | Authentication, project authorization, input validation, durable submission, monitoring, workflow definition/execution, and worker-management contracts. |
| PostgreSQL | Transactional, durable record of users, projects, tasks, attempts, events, workers, leases, workflows, nodes, edges, runs, and run nodes. It is used to recover or reconcile queue state. |
| Redis | Low-latency dispatch queues, short-lived coordination, and temporary scheduling/claim signals. It never replaces durable task state. |
| Scheduler | Future design component that finds due `scheduled_at` tasks and makes them queueable; recurring schedules are out of V1 scope. |
| Worker runtime | Independently running processes that register, claim work with leases, execute with timeouts, renew leases, report heartbeats, retry failures with backoff, and recover stale work. |
| Workflow engine | Validates DAG definitions (Kahn's algorithm), determines ready nodes from persisted dependencies, creates/dispatches task work via Redis, evaluates failure policies (FAIL_FAST / CONTINUE), and orchestrates multi-step pipelines. |
| Prometheus | Scrapes reproducible metrics from API and workers; no performance values are invented. |
| Grafana | Displays operational and benchmark dashboards using Prometheus data. |
| OpenTelemetry | Carries trace context from API request through dispatch and worker execution; structured logs include correlation IDs. |
| Docker | Later provides repeatable local and VM deployment packaging; it is not configured in this phase. |

## 3. Data flow and task submission

### Phase 5 workflow pipeline

```text
Client → POST /v1/workflows (Define DAG)
  ↓
FastAPI: authenticate, check project access
  ↓
Workflow Engine: Kahn's algorithm cycle detection & schema validation
  ↓
PostgreSQL: persist workflows, workflow_nodes, workflow_edges
  ↓
Client → POST /v1/workflows/{id}/run (Trigger Run)
  ↓
Workflow Engine: instantiate workflow_run and workflow_run_nodes (PENDING)
  ↓
Workflow Engine: identify root nodes (no incoming edges)
  ↓
Atomic Claim: UPDATE workflow_run_nodes SET status='RUNNING' WHERE status='PENDING'
  ↓ (If claimed: create Task row in PostgreSQL with status=QUEUED)
Redis: RPUSH task_queue {"task_id": "<UUID>"}
  ↓
Workers: BLPOP task_queue → atomic task claim → execute handler
  ↓
Worker: transition task to SUCCESS or FAILED
  ↓
Worker Hook (_try_advance_workflow):
  1. Update workflow_run_nodes status (SUCCESS/FAILED)
  2. If FAIL_FAST and failed: bulk-skip all PENDING nodes, fail run
  3. If CONTINUE: evaluate all PENDING nodes whose dependencies are all SUCCESS
  4. Atomically dispatch ready nodes (create Task → RPUSH task_queue)
  5. Repeat until no state changes; mark workflow_run terminal if all nodes complete
```

The API validates the task type, handler payload, project access, priority, timeout, scheduling, retry policy, and idempotency key. It persists the task before publishing its ID to Redis. A client receives a task ID rather than waiting for execution.

### Database/Redis consistency and reconciliation (Phase 4 & 5)

The durability boundary is PostgreSQL (ADR-001, ADR-007, ADR-008, ADR-009, ADR-010):

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

Workflow definitions persist nodes and directed edges. Creation validates that the graph is acyclic using Kahn's algorithm. At run time the workflow engine creates a `workflow_run` and per-run `workflow_run_nodes`; a node becomes `READY`/`RUNNING` only after all required predecessors succeeded. Independent ready nodes are submitted concurrently across available workers. Configured failure policies (`FAIL_FAST` vs. `CONTINUE`) govern behavior when a branch fails.

## 6. Observability and security boundaries

Prometheus will expose counters (`tasks_submitted_total`, success/failure/retry/worker-failure totals), histograms (`task_queue_wait_seconds`, `task_execution_seconds`, API/workflow duration), and gauges (queue depth, active workers, running tasks, available slots). Queue wait is measured from `queued_at` to the successful `RUNNING` claim; execution time is from `started_at` to `finished_at`. OpenTelemetry links API, queue-dispatch, and worker spans. Grafana is visualization only.

The browser is an untrusted client. The API authenticates users/API keys, applies RBAC and project-level authorization, limits payloads, validates schemas, and protects secrets. Workers only execute registered safe handlers; untrusted code is not accepted. PostgreSQL and Redis are private service boundaries. Production later adds TLS, secret management, rate limits, non-root containers, and resource limits.

## 7. Database schema design

| Table | Purpose, key columns, relationships, and indexes |
|---|---|
| `users` | Users. PK `id`; `email` (unique), `password_hash`, `role`, `created_at`. Owns projects and API keys; unique email index. Passwords are hashed, never plaintext. |
| `projects` | Authorization/ownership scope. PK `id`; FK `owner_id -> users`, `name`, `status`, `created_at`. Index `owner_id`; unique owner/name if product rules require it. |
| `api_keys` | Machine credentials. PK `id`; FK `user_id -> users`, `key_hash`, `name`, `last_used_at`, `revoked_at`, `created_at`. Unique key-hash and `user_id` indexes. |
| `tasks` | Authoritative task record. PK `id`; FK `project_id -> projects`, nullable FK `workflow_run_node_id -> workflow_run_nodes`; `type`, `payload_json`, `status`, `priority`, `idempotency_key`, `scheduled_at`, `timeout_seconds`, `max_retries`, `attempt_count`, `worker_id`, `lease_acquired_at`, `lease_expires_at`, `last_heartbeat_at`, `created_at`, `queued_at`, `started_at`, `finished_at`, result/error summary. Indexes: `(project_id,status)`, `(status,priority,created_at)`, `(scheduled_at)`, `(status,lease_expires_at)`, unique `(project_id,idempotency_key)`. |
| `task_attempts` | One execution attempt. PK `id`; FKs `task_id -> tasks`, nullable `worker_id -> workers`; `attempt_no`, `status`, `started_at`, `finished_at`, `error_code`, `error_message`, result metadata, lease token. Unique `(task_id,attempt_no)` and index `(task_id)`. |
| `task_events` | Immutable task timeline/audit. PK `id`; FK `task_id -> tasks`; `event_type`, `actor_id`/`worker_id`, `metadata_json`, `created_at`. Index `(task_id,created_at)`. |
| `workers` | Worker registry/liveness. PK `id`; `hostname`, `status`, `started_at`, `last_heartbeat_at`, `stopped_at`. Index `(status,last_heartbeat_at)` for health scans. |
| `workflows` | Reusable DAG definition. PK `id`; FK `project_id -> projects`; `name`, `failure_policy`, `created_at`. Index `(project_id)`. |
| `workflow_nodes` | Nodes in a workflow definition. PK `id`; FK `workflow_id -> workflows`; `node_key`, `task_type`, `payload_json`, `timeout_seconds`, `max_retries`. Unique `(workflow_id,node_key)` and index `(workflow_id)`. |
| `workflow_edges` | Directed dependencies. PK `id`; FKs `workflow_id -> workflows`, `from_node_id -> workflow_nodes`, `to_node_id -> workflow_nodes`. Unique `(workflow_id,from_node_id,to_node_id)` and index `(workflow_id,to_node_id)`. |
| `workflow_runs` | Execution instance of a workflow. PK `id`; FK `workflow_id -> workflows`; `status`, `failure_policy`, `started_at`, `finished_at`, `error_message`. Index `(workflow_id,started_at)`. |
| `workflow_run_nodes` | Per-run node execution state. PK `id`; FKs `workflow_run_id -> workflow_runs`, `workflow_node_id -> workflow_nodes`, nullable FK `task_id -> tasks`; `status`, `started_at`, `finished_at`, `error_message`. Unique `(workflow_run_id,workflow_node_id)` and index `(workflow_run_id,status)`. |
ce principle. |

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
