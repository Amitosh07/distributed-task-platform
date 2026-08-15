# Distributed Task Execution & Workflow Platform

Phase 3 implements concurrent execution across multiple worker processes.
Tasks submitted through the API are persisted in PostgreSQL, enqueued in Redis,
and distributed across concurrent workers that safely claim and execute tasks.

## Architecture (Phase 3)

```
                          ┌────────────────────────────────┐
                          │ Worker 1 (--worker-id worker-1)│
                          └──────────────┬─────────────────┘
                                         │
Client                                   │
  ↓                                      │
FastAPI  ───────────────► PostgreSQL ◄───┼ (atomic claim & result persistence)
  │ (persist QUEUED,      (source of     │
  │  then publish ID)      truth)        │
  ↓                                      │
Redis (task_queue) ──────────────────────┤
  (shared dispatch queue)                │
                                         ▼
                          ┌────────────────────────────────┐
                          │ Worker 2 (--worker-id worker-2)│
                          └────────────────────────────────┘
```

PostgreSQL is the authoritative source of truth. Redis is the shared dispatch queue.
Workers compete for task messages via `BLPOP` and perform an atomic conditional
`UPDATE tasks SET status='RUNNING' WHERE id=:task_id AND status='QUEUED'`
claim in PostgreSQL, guaranteeing that only one worker executes each task.

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

## Starting multiple workers

Each worker process is an independent process that consumes from the shared `task_queue`.
Give each worker a unique `--worker-id`:

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

**Additional workers (optional):**
```powershell
python -m app.workers.runtime --worker-id worker-3
```

You can also specify the worker ID via the `WORKER_ID` environment variable:
```powershell
$env:WORKER_ID = "worker-1"
python -m app.workers.runtime
```

## Worker identity and logging

All worker log lines include structured key-value pairs showing the worker ID,
task ID, and lifecycle event:

```text
2026-08-15T19:00:01 [INFO] worker_id=worker-1 task_id=... event=task_received
2026-08-15T19:00:01 [INFO] worker_id=worker-1 task_id=... event=task_claimed
2026-08-15T19:00:01 [INFO] worker_id=worker-1 task_id=... event=task_started type=sleep
2026-08-15T19:00:06 [INFO] worker_id=worker-1 task_id=... event=task_succeeded
```

If two workers race for the same task, the losing worker logs:
```text
2026-08-15T19:00:01 [INFO] worker_id=worker-2 task_id=... event=task_claim_lost task was already claimed by another worker or is not QUEUED
```

## Concurrency benchmark

Measure real multi-worker speedup:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://<user>:<password>@localhost:5432/workflow_platform"
$env:REDIS_URL    = "redis://localhost:6379/0"
cd backend
.\.venv\Scripts\python.exe ..\benchmarks\worker_concurrency.py
```

This runs a fixed batch of 8 sleep tasks (1.0s each) across 1, 2, and 4 workers,
outputting wall-clock time, throughput, and speedup metrics.

## Submitting tasks (manual multi-worker verification)

Using Swagger UI (`http://127.0.0.1:8000/docs`) or curl:

1. Register + Login to get a JWT.
2. Create a project (`POST /v1/projects`).
3. Submit 4 concurrent sleep tasks (`POST /v1/tasks` with `{"type": "sleep", "payload": {"seconds": 5}}`).
4. Watch both worker terminals process tasks concurrently.
5. Query PostgreSQL to verify all tasks reach `SUCCESS`:

```sql
SELECT id,
       type,
       status,
       started_at,
       finished_at,
       result_summary
FROM tasks
ORDER BY created_at DESC;
```

## Supported task types

| Type | Payload | Description |
|---|---|---|
| `sleep` | `{"seconds": <0..300>}` | Sleeps for N seconds. Used for demonstrating concurrency. |
| `csv_stats` | `{"csv_data": "<CSV string>"}` | Returns row count, column count, column names. Max 100 KB. |
| `image_resize` | `{"image_b64": "<base64>", "width": <int>, "height": <int>}` | Resizes image. Returns metadata. Max 5 MB input, 4096 px per axis. |
| `http_check` | `{"url": "<https://...>"}` | Checks HTTP reachability. No private IPs. Max 30s timeout. |

## Running tests

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://<user>:<password>@localhost:5432/workflow_platform_test"
$env:TEST_REDIS_URL    = "redis://localhost:6379/1"
cd backend
.\.venv\Scripts\pytest.exe -v
```

---

Heartbeats, worker leases, retries, workflows, the React dashboard,
observability, Docker services, and deployment remain planned future phases.
