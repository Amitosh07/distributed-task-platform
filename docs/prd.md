PRODUCT REQUIREMENTS DOCUMENT
Distributed Task Execution & Workflow Platform
Full-stack distributed-systems project blueprint
Version 1.0 • Implementation-oriented • August 2026

1. Executive Summary
Build a production-style platform that accepts computational tasks and multi-step workflows, queues and schedules them, executes them across independently running workers, persists durable state, and exposes monitoring, logs, retries and failure recovery through a web dashboard.
The primary SWE value is the backend/distributed-systems engineering; React makes the system observable and usable. Start as a modular monolith plus worker processes. Docker Compose is the local baseline; cloud deployment comes later.
2. Goals and Non-Goals
Goals
•	Submit tasks through REST APIs and receive a task ID immediately.
•	Queue tasks and execute them asynchronously on multiple workers.
•	Support priorities, retries, timeouts, scheduling and status tracking.
•	Register workers and detect worker failure with heartbeats.
•	Recover tasks after worker failure without silently losing work.
•	Support dependency-aware workflows represented as DAGs.
•	Provide task/workflow logs, execution history and worker health in a React dashboard.
•	Measure throughput, latency, queue depth, recovery behavior and scaling.
•	Containerize the complete system and deploy a reproducible cloud environment.
•	Produce reproducible quantitative metrics for a SWE resume.
Non-Goals for V1
•	Do not recreate Kubernetes, Airflow or Temporal feature-for-feature.
•	Do not begin with Kafka, Kubernetes or microservices unless measurement justifies them.
•	Do not claim exactly-once execution for arbitrary external side effects; use at-least-once delivery plus idempotency.
•	Do not run arbitrary untrusted user code directly on workers. Start with fixed safe task types or isolated containers.
3. Target Users and Use Cases
Actor	Use cases
Developer/API client	Submit tasks, create workflows, inspect status/results, schedule execution.
Worker	Register, heartbeat, claim tasks, execute handlers, report results/failures.
Operator	Monitor queue depth, workers, failures, retries and health.
Admin	Manage users/API keys, task definitions and configuration.

4. Recommended Tech Stack
Layer	Technology	Why
Frontend	React + TypeScript + Vite	Dashboard + type safety
UI	Tailwind CSS + component library	Fast consistent UI
API	Python + FastAPI	Async APIs, validation, OpenAPI
ORM	SQLAlchemy 2.x	Explicit relational modeling
Validation	Pydantic v2	Typed schemas/config
Database	PostgreSQL	Durable transactional state
Queue/coordination	Redis	Fast queueing and coordination
Workers	Python processes/containers	Simple horizontal scaling
HTTP client	httpx	Async HTTP
Migrations	Alembic	Versioned schema
Testing	pytest + pytest-asyncio + Testcontainers	Unit/integration testing
Load testing	Locust or k6	Concurrent load
Metrics	Prometheus	Time-series metrics
Dashboards	Grafana	Operations/benchmark UI
Tracing	OpenTelemetry	Distributed trace correlation
Containers	Docker + Compose	Reproducible environments
CI/CD	GitHub Actions	Automated test/build/deploy
Reverse proxy	Caddy or Nginx	TLS/routing
Cloud	Linux VM first; managed Postgres/Redis optional	Low cost/simple

5. High-Level Architecture
Browser
  │
  ▼
React Dashboard
  │
  ▼
FastAPI API ─────────────── PostgreSQL (durable source of truth)
  │
  └────────────── Redis (queues + coordination)
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
       Worker 1  Worker 2  Worker N
          │         │         │
          └─────────┴─────────┘
                    │
              Task execution

Prometheus/Grafana observe API, queue and workers.
Core rule: PostgreSQL is authoritative for durable state. Redis accelerates queueing/coordination; Redis failure must not make the durable database state meaningless.
6. Functional Requirements
Authentication & authorization
•	Use user login or API keys for machine clients.
•	Roles: admin, operator, developer/client.
•	Enforce project-level authorization on every protected resource.
•	Hash passwords with Argon2id/bcrypt; never store plaintext secrets.
•	Rotate/revoke API keys.
Task submission
•	POST /v1/tasks creates a task and returns task_id.
•	Task includes type, payload, priority, optional scheduled_at, timeout, max_retries and idempotency_key.
•	Validate task type and payload before enqueueing.
•	Persist task state before publishing; provide reconciliation for DB/Redis inconsistency.
Task lifecycle
CREATED → QUEUED → RUNNING → SUCCESS
                         └→ RETRY_WAIT → QUEUED
                         └→ FAILED / DEAD_LETTER
                         └→ CANCELLED / TIMED_OUT
•	Persist every transition with timestamps.
•	Store attempt count, worker ID, start/end times and errors.
•	Reject invalid transitions.
•	Expose a task event timeline.
Priority
•	HIGH/NORMAL/LOW initially.
•	Use aging or bounded priority to prevent starvation.
•	Measure queue wait separately from execution time.
Retries
•	Configurable max attempts, default 3.
•	Exponential backoff with jitter.
•	Retry transient failures; do not blindly retry validation errors.
•	Move exhausted tasks to DEAD_LETTER.
Workers
•	Register worker ID, version, capabilities and concurrency limit.
•	Heartbeat every few seconds.
•	Declare unhealthy after a configurable timeout.
•	Use leases/visibility timeout so crashed workers do not hold tasks forever.
Idempotency
•	Idempotency key prevents duplicate logical submissions.
•	Handlers should use task IDs/operation IDs to detect duplicate execution.
•	Document at-least-once delivery clearly.
Scheduling
•	Immediate execution.
•	Future timestamp execution.
•	Recurring schedules are optional V2.
Workflows/DAGs
•	Create nodes and dependencies.
•	Validate acyclic graph before acceptance.
•	A node becomes ready only when required dependencies succeed.
•	Independent ready nodes execute concurrently.
•	Define fail-fast/continue policy.
•	Show graph and run history in UI.
7. Safe Task Model
Do not expose arbitrary Python execution to public users in V1. Use a fixed handler registry.
TASK_HANDLERS = {
    "sleep": sleep_handler,
    "csv_stats": csv_stats_handler,
    "image_resize": image_resize_handler,
    "http_check": http_check_handler,
}
•	Validate every handler's input/output schema.
•	For dangerous processing, use isolated containers with CPU/memory/time limits.
•	Later support worker capability labels such as cpu, gpu or memory-heavy.
8. Database Design
Table	Important fields
users	id, email, password_hash, role, created_at
projects	id, owner_id, name, status, created_at
api_keys	id, user_id, key_hash, name, last_used_at, revoked_at
tasks	id, project_id, type, payload_json, status, priority, idempotency_key, scheduled_at, timeout_seconds, max_retries, attempt_count, created_at, started_at, finished_at
task_attempts	id, task_id, worker_id, attempt_no, status, started_at, finished_at, error_code
task_events	id, task_id, event_type, actor/worker_id, metadata_json, created_at
workers	id, hostname, version, status, capabilities_json, concurrency_limit, last_heartbeat_at
task_leases	task_id, worker_id, lease_expires_at, acquired_at
workflows	id, project_id, name, status, created_at
workflow_nodes	id, workflow_id, node_key, task_type, payload_json, status
workflow_edges	workflow_id, from_node_id, to_node_id
workflow_runs	id, workflow_id, status, started_at, finished_at

Recommended indexes: tasks(project_id,status), tasks(status,priority,created_at), tasks(scheduled_at), task_attempts(task_id), task_events(task_id,created_at), workers(status,last_heartbeat_at), workflow_nodes(workflow_id,status). Add indexes based on EXPLAIN ANALYZE.
9. API Design
Method	Endpoint	Purpose
POST	/v1/auth/login	Authenticate
POST	/v1/tasks	Submit task
GET	/v1/tasks/{task_id}	Task state
GET	/v1/tasks	Filter/paginate
POST	/v1/tasks/{task_id}/cancel	Cancel
POST	/v1/tasks/{task_id}/retry	Manual retry
GET	/v1/tasks/{task_id}/logs	Logs
POST	/v1/workflows	Create/validate workflow
POST	/v1/workflows/{id}/run	Start workflow
GET	/v1/workflows/{id}/runs/{run_id}	Inspect run
POST	/v1/workers/register	Register worker
POST	/v1/workers/{id}/heartbeat	Heartbeat
GET	/v1/workers	Worker health
GET	/health/live	Liveness
GET	/health/ready	Readiness

10. Queue and Worker Protocol
1. Validate request.
2. Persist task in PostgreSQL.
3. Make task available in Redis.
4. Worker claims task and obtains lease.
5. Worker marks RUNNING.
6. Worker renews lease/heartbeat.
7. Handler executes.
8. Worker reports SUCCESS/FAILURE.
9. Persist result and release lease.
10. On timeout/worker loss, recover/requeue if attempts remain.
Test races: two workers claiming one task; completion after lease expiry; retry while old worker is still running; duplicate submission; DB/Redis inconsistency.
11. Frontend Requirements
•	Login and project selector.
•	Overview: queue depth, task counts, throughput, failures, worker health.
•	Task table with filters and pagination.
•	Task detail: state timeline, attempts, logs, result, retry/cancel.
•	Workflow Graph View; start read-only before drag-and-drop.
•	Worker page: heartbeat age, concurrency, current tasks, capabilities.
•	Start with polling; add SSE/WebSockets only after the basic system is stable.
12. Project Structure
backend/
  app/
    main.py config.py
    db/ models/ migrations/
    api/routes/ auth.py tasks.py workflows.py workers.py health.py
    schemas/
    services/ task_service.py scheduler.py workflow_engine.py retry_policy.py recovery.py idempotency.py
    queue/ redis_client.py publisher.py consumer.py
    workers/ runtime.py registry.py heartbeat.py handlers/
    observability/ metrics.py logging.py tracing.py
    tests/
  alembic/
frontend/src/
  pages/ components/ hooks/ services/ types/
docker-compose.yml
.github/workflows/
benchmarks/
docs/
13. Implementation Roadmap
Phase	Deliverables	Exit criterion
0 Design	Architecture, state machine, schema, API, ADRs	Can explain components/transitions
1 Core API	Auth, projects, task CRUD, Postgres	API tests pass
2 Queue	Redis, enqueue/dequeue, one worker	Reliable execution
3 Distributed workers	Multiple workers, leases, heartbeats	N workers execute concurrently
4 Reliability	Retries, backoff, timeout, recovery, idempotency	Injected failures recover
5 Workflows	DAG validation, dependencies, parallel branches	Correct dependency execution
6 Frontend	Dashboard, tasks, workers, graph	Usable without Postman
7 Observability	Prometheus, Grafana, logs, tracing	Can diagnose failures
8 Testing	Integration, race, failure, load	Reproducible test suite
9 Containers	Dockerfiles, Compose, health checks	One command starts stack
10 Cloud	HTTPS, secrets, deployment, backups	Public staging works
11 Optimization	Profile, indexes, queue tuning, worker scaling	Before/after benchmarks
12 Polish	Docs, demo, architecture, resume metrics	Recruiter-ready repo

14. Testing Strategy
Type	Examples
Unit	State transitions, retry policy, DAG validation, priority
API integration	Auth, CRUD, pagination, idempotency
Worker integration	Claim/lease/heartbeat/result with Redis/Postgres
Failure injection	Kill worker, Redis outage, DB transient failure, timeout
Concurrency	Many workers race for same task; duplicate submissions
Workflow	Branches, dependency failures, retries, parallelism
Security	Invalid tokens, privilege escalation, validation, rate limits
Load	Concurrent submissions, sustained throughput, dashboard queries
Soak	Long-running workers and queues

15. Benchmark & Resume Metrics Plan
Never invent performance numbers. Each resume metric should map to a reproducible benchmark configuration and result artifact.
Benchmark	Measure	Resume-ready metric
API load	RPS, P50/P95/P99, errors	1,000 concurrent clients; X req/s; Y ms P95
Task throughput	Tasks/sec, completion time	50K tasks across 20 workers at X tasks/sec
Scaling	1/5/10/20 worker throughput	X% improvement from 5 to 20 workers
Failure recovery	Injected failures, recovered tasks, time	X/X recovered, 0 lost tasks
Retries	Transient failure recovery	X% injected failures recovered
DB optimization	Before/after query latency	X% P95 reduction
Queue optimization	Queue wait/dispatch latency	X→Y ms P95
Real usage	Users/tasks/workflows	X real tasks for Y users

Benchmark scenarios
•	10K tasks with 1, 5, 10 and 20 workers.
•	50K task burst.
•	1K concurrent API clients.
•	5–10% injected transient failures.
•	Kill workers during execution and verify recovery.
•	Duplicate submissions with same idempotency key.
•	DAGs with parallel branches and dependency failures.
•	Dashboard query benchmark at 1K/10K/100K task rows.
Store raw results under benchmarks/results/ with timestamp, commit SHA, environment, worker count, task count and tool version.
16. Observability
•	Counters: tasks_submitted_total, tasks_success_total, tasks_failed_total, task_retries_total, worker_failures_total.
•	Histograms: task_queue_wait_seconds, task_execution_seconds, API duration, workflow duration.
•	Gauges: queue_depth, active_workers, running_tasks, available_worker_slots.
•	Structured logs include request_id, task_id, workflow_run_id, worker_id and event type.
•	Trace API request → task creation → queue dispatch → worker execution with OpenTelemetry.
17. Security
•	TLS in production.
•	Secrets only via environment/secret manager; never commit .env.
•	Argon2id/bcrypt for passwords.
•	JWT expiry or short-lived API tokens.
•	RBAC and project-level authorization.
•	Strict validation and payload size limits.
•	Rate-limit auth/public endpoints.
•	Never execute arbitrary uploaded code on host.
•	Non-root containers and CPU/memory/time limits.
•	Dependency and secret scanning in CI.
18. Docker & Deployment
Local baseline: Docker Compose for API, worker(s), PostgreSQL, Redis, frontend, Prometheus and Grafana.
services:
  api:
    build: ./backend
  worker:
    build: ./backend
    command: python -m app.workers.runtime
  postgres:
    image: postgres
  redis:
    image: redis
  frontend:
    build: ./frontend
  prometheus:
    image: prom/prometheus
  grafana:
    image: grafana/grafana
Cloud V1: one Linux VM for API/workers plus managed PostgreSQL/Redis if affordable. Use Docker Compose, Caddy/Nginx, HTTPS, backups and monitoring. Do not start with Kubernetes.
19. CI/CD
•	PR: lint, type checks, unit tests, integration tests where practical, frontend build.
•	Main: build versioned images and validate migrations.
•	Deploy staging automatically after checks.
•	Production initially requires explicit approval.
•	Use GitHub Actions secrets.
•	Tag releases and record Git SHA in app/worker version.
20. Performance Optimization
•	Async FastAPI for I/O-bound work.
•	SQLAlchemy pooling; eliminate N+1 queries.
•	Pagination.
•	Index based on EXPLAIN ANALYZE.
•	Keep large results out of Redis; use object storage/database.
•	Bound worker concurrency.
•	Backpressure when queue depth grows.
•	Cache only safe expensive data.
•	Benchmark every optimization before/after.
21. Fault-Tolerance Matrix
Failure	Expected behavior
Worker crash	Lease expires; task becomes recoverable
Task exception	Retry if retryable; otherwise dead-letter
Timeout	Record TIMEOUT; stop handler where safe; retry by policy
Duplicate submission	Same idempotency key returns original task
Redis outage	Fail gracefully; durable state remains in PostgreSQL; reconciliation requeues missing work
DB transient outage	Bounded retry; controlled API error; no acknowledgement without durable state
Stale worker report	Reject completion using lease/attempt token
Workflow dependency failure	Apply fail-fast/continue policy and preserve causal error

22. Distributed-Systems Concepts to Learn
•	At-least-once delivery and why exactly-once is difficult.
•	Idempotency and deduplication.
•	Leases, heartbeats and failure detection.
•	Race conditions and atomic state transitions.
•	Transactions and consistency boundaries.
•	Backpressure and queue depth.
•	Retries, exponential backoff and retry storms.
•	Dead-letter queues.
•	DAG scheduling/topological ordering.
•	Horizontal scaling and worker concurrency.
•	Eventual consistency.
•	Observability: logs, metrics and traces.
23. Learning Order
•	FastAPI + Pydantic + SQLAlchemy + PostgreSQL.
•	Redis queues/coordination.
•	Python process/concurrency basics.
•	Docker/Compose.
•	PostgreSQL transactions, indexes, EXPLAIN.
•	Distributed systems: leases, retries, idempotency, failure models.
•	DAGs and workflow scheduling.
•	Prometheus/Grafana/OpenTelemetry.
•	Locust/k6.
•	Cloud deployment, TLS, secrets, backups, CI/CD.
24. Definition of Done
•	Task submission returns immediately with task ID.
•	At least 3 workers execute concurrently.
•	Durable task state in PostgreSQL.
•	Redis dispatch plus reconciliation path.
•	Heartbeat-based worker failure detection.
•	Lease-based task recovery.
•	Retry/backoff/dead-letter tested.
•	Duplicate submissions are idempotent.
•	Non-trivial DAG with parallel branches executes correctly.
•	React dashboard shows tasks, workflows, queue and worker health.
•	Metrics/logs/health checks available.
•	Automated tests cover core state and failure paths.
•	Reproducible load/fault benchmarks exist.
•	Docker Compose starts complete local environment.
•	Cloud staging is HTTPS-accessible.
•	README documents architecture, tradeoffs, benchmarks and limitations.
25. Resume Positioning
Project title: Distributed Task Execution & Workflow Platform
Suggested stack: Python, FastAPI, React, PostgreSQL, Redis, Docker, GitHub Actions, Prometheus/Grafana
•	Built a distributed task execution and workflow platform with priority scheduling, retries, worker heartbeats, leases, failure recovery and dependency-aware DAG execution.
•	Processed [N]+ benchmark tasks across [N] workers at [X] tasks/sec with [Y] ms P95 execution/queue latency.
•	Implemented fault-injection testing for worker crashes and transient failures, recovering [X%] of affected tasks with [0 or N] lost tasks.
•	Optimized PostgreSQL/Redis paths and worker concurrency, improving [measured metric] by [X%].
Replace every placeholder only after running the benchmark. Prefer 'load-tested' or 'benchmarked' when the number comes from simulation rather than real users.
26. Architecture Decision Records
•	ADR-001: PostgreSQL as source of truth.
•	ADR-002: Redis for queueing/coordination.
•	ADR-003: At-least-once + idempotency instead of claiming exactly-once.
•	ADR-004: Lease/heartbeat design.
•	ADR-005: Docker Compose/VM before Kubernetes.
•	ADR-006: Fixed/sandboxed task handlers in V1.
27. Final Scope Recommendation
Build in this order: task API → PostgreSQL → Redis queue → one worker → multiple workers → heartbeats/leases → retries/timeouts → idempotency → workflow DAGs → React dashboard → Prometheus/Grafana → Docker/CI → cloud deployment → load/fault testing → optimization.
The strongest version is not the one with the most technologies. It is the one where you can demonstrate a real failure, measure before/after a design change, explain why each component exists, and reproduce the result.
