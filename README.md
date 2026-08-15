# Distributed Task Execution & Workflow Platform

Phase 5 implements Workflow Directed Acyclic Graphs (DAGs): graph cycle validation via Kahn's algorithm, dependency-aware execution, parallel branch dispatch, atomic duplicate-dispatch prevention, and customizable failure policies (`FAIL_FAST` / `CONTINUE`).

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

### Run Phase 5 tests only:
```powershell
cd backend
.\.venv\Scripts\pytest.exe -v app/tests/test_phase5_dag_validation.py app/tests/test_phase5_workflow_api.py app/tests/test_phase5_workflow_execution.py app/tests/test_phase5_failure_policy.py app/tests/test_phase5_concurrency.py
```

## Running the Workflow DAG Benchmark

Measures sequential vs parallel branch speedup with real multi-worker processes:

```powershell
python benchmarks/workflow_dag_benchmark.py
```

---

The React dashboard, Prometheus/Grafana metrics, OpenTelemetry tracing, Docker Compose packaging, and cloud deployment remain planned future phases.
