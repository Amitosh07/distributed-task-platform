# Distributed Task Execution & Workflow Platform

Phase 4 implements reliable worker/task execution: worker heartbeats, task leases,
execution timeouts, exponential backoff retries, and concurrency-safe stale task recovery.

## Architecture (Phase 4)

```
                          ┌────────────────────────────────────┐
                          │   Worker 1 (--worker-id worker-1)  │
                          │   - Heartbeats to PostgreSQL       │
                          │   - Renews active task lease       │
                          └──────────────┬─────────────────────┘
                                         │
Client                                   │
  ↓                                      │
FastAPI  ───────────────► PostgreSQL ◄───┼ (atomic claim, leases, recovery)
  │ (persist QUEUED,      (source of     │
  │  then publish ID)      truth)        │
  ↓                                      │
Redis (task_queue) ──────────────────────┤
  (shared dispatch queue)                │
                                         ▼
                          ┌────────────────────────────────────┐
                          │   Worker 2 (--worker-id worker-2)  │
                          │   - Heartbeats to PostgreSQL       │
                          │   - Recovers expired stale tasks   │
                          └────────────────────────────────────┘
```

PostgreSQL is the authoritative source of truth. Redis is the shared dispatch queue.
Workers compete for task messages via `BLPOP` and perform an atomic claim that sets
an expiring lease (`lease_expires_at`). The worker renews this lease while running the task.
If a worker crashes, its lease expires; any healthy worker automatically recovers the task,
resets it to `QUEUED`, and re-enqueues it on Redis.

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

### Phase 4 Reliability Configuration (Optional Overrides)

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

## Redis setup (WSL2)

Redis runs inside WSL2 Ubuntu. Start it with:

```bash
# In WSL2 terminal
redis-server --daemonize yes
redis-cli ping   # should return PONG
```

Verify from Windows PowerShell:

```powershell
Test-NetConnection localhost -Port 6379
# TcpTestSucceeded : True
```

## Starting the API

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

API is available at `http://127.0.0.1:8000`. Swagger UI at `http://127.0.0.1:8000/docs`.

## Starting workers

Each worker process is an independent process that registers in PostgreSQL and consumes
from the shared `task_queue`. Give each worker a unique `--worker-id`:

**Terminal 2 (Worker 1):**
```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m app.workers.runtime --worker-id worker-1
```

**Terminal 3 (Worker 2):**
```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m app.workers.runtime --worker-id worker-2
```

## Worker failure & recovery simulation (manual test)

1. Start `worker-1` and `worker-2` in separate terminals.
2. Submit a 15-second sleep task (`POST /v1/tasks` with `{"type": "sleep", "payload": {"seconds": 15}}`).
3. Note in the terminal that `worker-1` claims the task and starts heartbeating.
4. Kill `worker-1` (press Ctrl+C or terminate terminal).
5. Watch `worker-2` logs: as soon as `worker-1`'s lease expires, `worker-2`'s background recovery scanner detects the stale task, resets it to `QUEUED`, re-enqueues it to Redis, claims it, and executes it to `SUCCESS`.
6. Query PostgreSQL to inspect task attempts and state:

```sql
SELECT id,
       type,
       status,
       worker_id,
       attempt_count,
       started_at,
       finished_at,
       result_summary
FROM tasks
ORDER BY created_at DESC;
```

## Checking worker health

Inspect registered workers and heartbeats via API:
- `GET /v1/workers` (requires Authorization bearer token)

Or query PostgreSQL directly:
```sql
SELECT id, hostname, status, started_at, last_heartbeat_at, stopped_at
FROM workers
ORDER BY started_at DESC;
```

## Running tests

Tests require a dedicated PostgreSQL database (`workflow_platform_test`) and Redis DB 1.
They will never touch the development database or Redis DB 0.

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://<user>:<password>@localhost:5432/workflow_platform_test"
$env:TEST_REDIS_URL    = "redis://localhost:6379/1"
cd backend
.\.venv\Scripts\pytest.exe -v
```

---

Workflows (DAGs), the React dashboard, Prometheus/Grafana metrics, OpenTelemetry tracing,
Docker Compose packaging, and cloud deployment remain planned future phases.
