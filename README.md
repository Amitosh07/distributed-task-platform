# Distributed Task Execution & Workflow Platform

Phase 1 implements a PostgreSQL-backed FastAPI foundation: JWT authentication,
user-owned projects, durable task creation/retrieval/listing, Alembic migrations,
and PostgreSQL readiness checks. New tasks are persisted with `CREATED` status;
they are not queued or executed yet.

## Local backend setup

PostgreSQL is required. Create a database, then copy `backend/.env.example` to
`backend/.env` and replace every placeholder. Never commit the real `.env` file.

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
alembic upgrade head
uvicorn app.main:app --reload
```

Required environment variables: `DATABASE_URL`, `JWT_SECRET_KEY`,
`JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, and `ENVIRONMENT`.

## Tests

Tests require a dedicated disposable PostgreSQL database and will not use the
development database. Set `TEST_DATABASE_URL` before running them; the suite
runs Alembic down/up around the session and truncates data between tests.

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://platform:password@localhost:5432/distributed_task_platform_test"
cd backend
pytest
```

## Phase 1 endpoints

- `POST /v1/auth/register`, `POST /v1/auth/login`, `GET /v1/auth/me`
- `POST /v1/projects`, `GET /v1/projects`, `GET /v1/projects/{project_id}`
- `POST /v1/tasks`, `GET /v1/tasks`, `GET /v1/tasks/{task_id}`
- `GET /health/live`, `GET /health/ready`

Redis, workers, workflows, the React dashboard, observability, Docker services,
and deployment remain planned future phases.
