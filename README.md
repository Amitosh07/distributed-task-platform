# Distributed Task Execution & Workflow Platform

Phase 2 implements Redis-backed asynchronous task execution with one worker process.
Tasks submitted through the API are persisted in PostgreSQL, enqueued in Redis,
and executed asynchronously by the worker.

## Architecture (Phase 2)

```
Client
  ↓
FastAPI  ─────────────────────────────────────► PostgreSQL (durable state)
  │  (persist QUEUED, then publish task ID)          ▲
  ↓                                                   │
Redis (task_queue)                                    │
  │                                                   │
  ▼                                                   │
Worker (python -m app.workers.runtime)                │
  │  (load from PG, execute handler, persist result) ─┘
  ↓
RUNNING → SUCCESS / FAILED
```

PostgreSQL is the authoritative source of truth. Redis is a dispatch queue only.

## Local backend setup

Redis and PostgreSQL are required. Ensure both are running before starting the API
or the worker.

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

## Starting the worker

Open a second terminal:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m app.workers.runtime
```

The worker will log startup, connect to Redis and PostgreSQL, then wait for tasks.

## Submitting a task (manual test)

Using the Swagger UI or curl:

```json
POST /v1/tasks
{
  "project_id": "<your-project-id>",
  "type": "sleep",
  "payload": { "seconds": 5 },
  "priority": "NORMAL",
  "timeout_seconds": 30,
  "max_retries": 0
}
```

Response immediately returns the task in `QUEUED` status. Watch the worker terminal
for `RUNNING` → `SUCCESS`. Poll `GET /v1/tasks/{task_id}` to see the final state.

## Supported task types

| Type | Payload | Description |
|---|---|---|
| `sleep` | `{"seconds": <0..300>}` | Sleeps for N seconds. Use to demonstrate async execution. |
| `csv_stats` | `{"csv_data": "<CSV string>"}` | Returns row count, column count, column names. Max 100 KB. |
| `image_resize` | `{"image_b64": "<base64>", "width": <int>, "height": <int>}` | Resizes image. Returns metadata. Max 5 MB input, 4096 px per axis. |
| `http_check` | `{"url": "<https://...>"}` | Checks HTTP reachability. No private IPs. Max 30s timeout. |

## Checking task status in PostgreSQL

```sql
SELECT id,
       project_id,
       type,
       status,
       priority,
       queued_at,
       started_at,
       finished_at,
       result_summary,
       error_message
FROM tasks
ORDER BY created_at DESC;
```

## Health endpoints

- `GET /health/live` — liveness (always ok if the process is running)
- `GET /health/ready` — readiness (requires PostgreSQL + Redis)

## Tests

Tests require a dedicated PostgreSQL database (`workflow_platform_test`) and Redis
DB 1. They will never modify the development database or Redis DB 0.

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://<user>:<pass>@localhost:5432/workflow_platform_test"
$env:TEST_REDIS_URL    = "redis://localhost:6379/1"
cd backend
.\.venv\Scripts\pytest.exe -v
```

## API endpoints

### Auth
- `POST /v1/auth/register`, `POST /v1/auth/login`, `GET /v1/auth/me`

### Projects
- `POST /v1/projects`, `GET /v1/projects`, `GET /v1/projects/{project_id}`

### Tasks
- `POST /v1/tasks` — submit task (returns `QUEUED` immediately)
- `GET /v1/tasks` — list with filtering and pagination
- `GET /v1/tasks/{task_id}` — get task state

### Health
- `GET /health/live`, `GET /health/ready`

---

Redis, multiple workers, heartbeats, retries, workflows, the React dashboard,
observability, Docker services, and deployment remain planned future phases.
