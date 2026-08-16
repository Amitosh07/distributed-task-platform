# Distributed Task Execution & Workflow Platform

A production-quality distributed task execution and workflow orchestration platform built with **FastAPI**, **PostgreSQL**, **Redis**, and **React**. Phases 1–9 complete.

---

## Phase 9 — Docker Compose & Containerized Deployment

Phase 9 provides single-command containerized orchestration for the complete 8-service platform (PostgreSQL, Redis, FastAPI API, Worker, React Frontend, Prometheus, Grafana, OpenTelemetry Collector).

### 1-Command Startup

```powershell
# Build and start the entire stack (with 3 worker replicas) in the background
docker compose up --build -d --scale worker=3

# Apply initial database migrations
docker compose exec api alembic upgrade head
```

### Access Platform Services

| Service | URL / Port | Credentials / Notes |
|---|---|---|
| **Operations Dashboard (React)** | [http://localhost:5173](http://localhost:5173) | Browser UI for tasks, workers, workflows |
| **FastAPI REST API** | [http://localhost:8000](http://localhost:8000) | Root API endpoint |
| **Interactive API Docs (Swagger)** | [http://localhost:8000/docs](http://localhost:8000/docs) | OpenAPI interactive explorer |
| **Grafana Dashboard** | [http://localhost:3000](http://localhost:3000) | `admin` / `admin` (16 metrics panels) |
| **Prometheus Server** | [http://localhost:9090](http://localhost:9090) | Native metrics scraper & explorer |
| **OpenTelemetry Collector** | `localhost:4317` (gRPC), `4318` (HTTP) | Distributed trace collector |
| **PostgreSQL Database** | `localhost:5432` | `postgres` / `postgres` (`workflow_platform`) |
| **Redis Cache & Queue** | `localhost:6379` | DB 0 for tasks |

### Common Docker Compose Commands

```powershell
# View running services and health status
docker compose ps

# Stream logs from API or workers
docker compose logs -f api
docker compose logs -f worker

# Scale worker processes up or down dynamically
docker compose up -d --scale worker=5

# Stop the stack (PostgreSQL and Redis data is PRESERVED in named volumes)
docker compose down

# Stop the stack and DESTROY persistent database volumes (Clean reset)
docker compose down -v
```

---

## Phase 8 — Load Testing, Fault Injection & Benchmarking

Phase 8 adds a reproducible benchmarking and fault-injection framework to measure and validate the distributed system's behavior under load.

### Quick benchmark commands

```powershell
cd backend
.\.venv\Scripts\Activate.ps1

# Set environment
$env:DATABASE_URL = "postgresql+psycopg://<user>:<password>@localhost:5432/workflow_platform"
$env:REDIS_URL    = "redis://localhost:6379/0"

# Worker scaling sweep (1, 2, 3 workers × 100 tasks)
python benchmarks/load_test.py --tasks 100 --worker-counts 1,2,3 --seconds 1

# Single throughput benchmark (3 workers)
python benchmarks/load_test.py --tasks 100 --workers 3

# Fault injection
python benchmarks/fault_injection.py worker-crash
python benchmarks/fault_injection.py timeout-injection
python benchmarks/fault_injection.py redis-outage
python benchmarks/fault_injection.py retry-storm

# Workflow DAG scaling (linear / diamond / fan-out × 1,2,3 workers)
python benchmarks/workflow_scaling.py --dag all --workers 1,2,3 --seconds 0.5
python benchmarks/workflow_scaling.py --test-failure-policies

# Idempotency & concurrency stress
python benchmarks/idempotency_stress.py --concurrency 20 --rounds 2
```

All results are saved to `benchmarks/results/*.json`. See [docs/benchmarks.md](docs/benchmarks.md) for the full benchmark report, methodology, and result tables.

### Phase 8 test commands

```powershell
# Phase 8 unit tests (no DB/Redis required — always safe for CI)
cd backend
.\.venv\Scripts\pytest.exe -v app/tests/test_phase8_benchmarks.py

# Phase 8 integration tests (requires TEST_DATABASE_URL + TEST_REDIS_URL)
$env:TEST_DATABASE_URL = "postgresql+psycopg://<user>:<password>@localhost:5432/workflow_platform_test"
$env:TEST_REDIS_URL    = "redis://localhost:6379/1"
.\.venv\Scripts\pytest.exe -v app/tests/test_phase8_fault_injection.py app/tests/test_phase8_scaling.py
```

### Benchmark files created

| File | Purpose |
|---|---|
| `benchmarks/lib/common.py` | Shared helpers: DB fixtures, worker management, latency stats, JSON result saving |
| `benchmarks/load_test.py` | Unified load test with CLI (throughput, scaling sweep, p50/p95/p99) |
| `benchmarks/fault_injection.py` | Worker crash / timeout / Redis outage / retry storm |
| `benchmarks/workflow_scaling.py` | DAG shapes × worker counts, failure policies |
| `benchmarks/idempotency_stress.py` | Sequential/concurrent duplicates, dispatch stress |
| `benchmarks/results/` | Machine-readable JSON result artifacts |
| `backend/app/tests/test_phase8_benchmarks.py` | Unit tests (no DB) — latency stats, result schema |
| `backend/app/tests/test_phase8_fault_injection.py` | Integration fault tests (DB+Redis required) |
| `backend/app/tests/test_phase8_scaling.py` | Integration scaling/concurrency tests |
| `docs/benchmarks.md` | Full benchmark report with methodology and result tables |

---

## Dashboard (Phase 6)

The dashboard uses the FastAPI API; PostgreSQL remains the durable source of truth, Redis remains the dispatch/coordination layer, and workers still execute the tasks. It does not use WebSockets or SSE.

```text
Browser -> React dashboard -> FastAPI -> PostgreSQL / Redis -> Workers
```

### Run locally

Start PostgreSQL and Redis, then start the API and one or more workers in separate PowerShell windows:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
alembic upgrade head
uvicorn app.main:app --reload
```

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m app.workers.runtime --worker-id worker-1
```

Start a second worker with `--worker-id worker-2`. Create a user with `POST /v1/auth/register` once (the dashboard deliberately has no registration screen), then use that email and password to sign in.

```powershell
cd frontend
Copy-Item .env.example .env
pnpm install
pnpm dev
```

Open the URL printed by Vite (normally `http://localhost:5173`). Set `VITE_API_BASE_URL` in `frontend/.env` when FastAPI is not running at `http://localhost:8000`. The frontend stores only the bearer token and selected project in the browser session; it never stores a password.

The dashboard includes a project selector, task filters and server-side pagination, task detail/state data, worker heartbeat ages, workflow definitions, and read-only workflow run DAG status. Pages poll while open (workers every 5 seconds; overview/tasks every 8 seconds; task and workflow-run detail every 2.5 seconds).

## Observability (Phase 7)

FastAPI exposes Prometheus metrics at `http://localhost:8000/metrics`. API logs and worker logs are JSON, and every HTTP response returns `X-Request-ID` (preserving a caller-provided value). IDs are used in logs/traces only—not Prometheus labels—so metric cardinality stays bounded.

Start the local observability services after the API is running:

```powershell
docker compose up -d prometheus grafana otel-collector
```

- Prometheus: `http://localhost:9090` (target: `host.docker.internal:8000/metrics`)
- Grafana: `http://localhost:3000` (`admin` / `admin`, change this outside local development) — Phase 8 dashboard has 16 panels
- OTLP gRPC receiver: `localhost:4317`; set `OTEL_EXPORTER_OTLP_ENDPOINT=localhost:4317` in `backend/.env` to export traces to the local collector.

Prometheus/Grafana/OTLP are intentionally non-critical: if they are stopped, persistence, queueing, and workers continue. See [ADR-011](docs/adr/ADR-011-phase7-observability.md) for the metric-cardinality policy and architecture decision.

## Architecture (Phase 5)

```
                          ┌────────────────────────────────────┐
                          │   Worker 1 (--worker-id worker-1)  │
                          │   - Heartbeats to PostgreSQL       │
                          │   - Renews active task lease       │
                          │   - Advances workflow on complete  │
                          └──────────────┬─────────────────────┘
                                         │
Client                                   │
  ↓                                      │
FastAPI  ───────────────► PostgreSQL ◄───┼ (atomic claim, leases, recovery, runs)
  │ (validate DAG &       (source of     │
  │  dispatch ready        truth)        │
  │  tasks via Redis)                    │
  ↓                                      │
Redis (task_queue) ──────────────────────┤
  (shared dispatch queue)                │
                                         ▼
                          ┌────────────────────────────────────┐
                          │   Worker 2 (--worker-id worker-2)  │
                          │   - Heartbeats to PostgreSQL       │
                          │   - Recovers expired stale tasks   │
                          │   - Executes parallel DAG branches │
                          └────────────────────────────────────┘
```

PostgreSQL is the authoritative source of truth for workflow definitions, runs, and per-run node states (`workflow_run_nodes`). Redis is the shared dispatch queue. Ready nodes are dispatched as standard task records into Redis, and independent parallel branches execute concurrently across all available workers.

## Local backend setup

Redis and PostgreSQL are required. Ensure both are running before starting the API
or workers.

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
alembic upgrade head
```

Copy `backend/.env.example` to `backend/.env` and fill in all values. Never commit
the real `.env` file.

Required environment variables:

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string for `workflow_platform` |
| `JWT_SECRET_KEY` | Long random secret (≥ 32 chars) |
| `JWT_ALGORITHM` | Default: `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Default: `30` |
| `ENVIRONMENT` | `development` or `test` |
| `REDIS_URL` | Redis connection string. Default: `redis://localhost:6379/0` |

### Reliability Configuration (Optional Overrides)

| Variable | Default | Description |
|---|---|---|
| `HEARTBEAT_INTERVAL_SECONDS` | `2.0` | Frequency of worker heartbeats |
| `WORKER_STALE_THRESHOLD_SECONDS` | `10.0` | Duration after which inactive workers are marked `STALE` |
| `TASK_LEASE_SECONDS` | `10.0` | Initial duration and renewal extension for task leases |
| `RECOVERY_INTERVAL_SECONDS` | `5.0` | Frequency of background stale task and worker recovery |
| `DEFAULT_TASK_TIMEOUT_SECONDS` | `300` | Default execution timeout in seconds per task |
| `DEFAULT_MAX_RETRIES` | `3` | Default additional retry attempts for retryable failures |
| `RETRY_BACKOFF_BASE_SECONDS` | `1.0` | Base delay for exponential backoff (`base * 2^(attempt-1)`) |
| `RETRY_BACKOFF_MAX_SECONDS` | `60.0` | Maximum cap for retry backoff delay |

## Workflow DAG API (Phase 5)

### 1. Create a Workflow Definition (DAG)
`POST /v1/workflows`

```json
{
  "project_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "name": "Diamond Pipeline",
  "failure_policy": "FAIL_FAST",
  "nodes": [
    {"node_key": "A", "task_type": "sleep", "payload": {"seconds": 1.0}},
    {"node_key": "B", "task_type": "csv_stats", "payload": {"csv_data": "a,b\n1,2"}},
    {"node_key": "C", "task_type": "sleep", "payload": {"seconds": 1.0}},
    {"node_key": "D", "task_type": "http_check", "payload": {"url": "https://example.com"}}
  ],
  "edges": [
    {"from": "A", "to": "B"},
    {"from": "A", "to": "C"},
    {"from": "B", "to": "D"},
    {"from": "C", "to": "D"}
  ]
}
```

### 2. Trigger a Workflow Run
`POST /v1/workflows/{workflow_id}/run`

Returns `202 Accepted` with initial run state and root nodes dispatched.

### 3. Inspect Run Progress
`GET /v1/workflows/{workflow_id}/runs/{run_id}`

Returns real-time execution status of all nodes in the workflow run (`PENDING`, `RUNNING`, `SUCCESS`, `FAILED`, `SKIPPED`).

## Running tests

Tests require a dedicated PostgreSQL database (`workflow_platform_test`) and Redis DB 1.
They will never touch the development database or Redis DB 0.

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://<user>:<password>@localhost:5432/workflow_platform_test"
$env:TEST_REDIS_URL    = "redis://localhost:6379/1"
cd backend
.\.venv\Scripts\pytest.exe -v
```

### Run Phase 8 unit tests only (no DB required):
```powershell
cd backend
.\.venv\Scripts\pytest.exe -v app/tests/test_phase8_benchmarks.py
```

### Run Phase 5 tests only:
```powershell
cd backend
.\.venv\Scripts\pytest.exe -v app/tests/test_phase5_dag_validation.py app/tests/test_phase5_workflow_api.py app/tests/test_phase5_workflow_execution.py app/tests/test_phase5_failure_policy.py app/tests/test_phase5_concurrency.py
```

## Running Benchmarks (Phase 8)

See [docs/benchmarks.md](docs/benchmarks.md) for the complete benchmark report.

### Scaling benchmark:
```powershell
python benchmarks/load_test.py --tasks 100 --worker-counts 1,2,3 --seconds 1
```

### Workflow DAG benchmark:
```powershell
python benchmarks/workflow_scaling.py --dag all --workers 1,2,3 --seconds 0.5
```

### Fault injection:
```powershell
python benchmarks/fault_injection.py worker-crash
python benchmarks/fault_injection.py timeout-injection
python benchmarks/fault_injection.py redis-outage
python benchmarks/fault_injection.py retry-storm
```

---
