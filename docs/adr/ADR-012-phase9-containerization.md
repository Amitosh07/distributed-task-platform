# ADR-012: Phase 9 — Containerization, Docker Compose & Health Checks

## Status

Accepted

## Context

The Distributed Task Execution & Workflow Platform has completed Phases 1–8:
- PostgreSQL authoritative persistence
- Redis task queue and worker coordination
- Concurrency control, leases, heartbeats, and recovery
- DAG workflow engine
- React operations dashboard
- Prometheus, Grafana, OpenTelemetry observability
- Load testing, fault injection, and benchmarking

Phase 9 establishes reproducible containerization and local deployment tooling. The core requirement is that one command (`docker compose up --build`) brings up the complete 8-service system in a deterministic, observable state.

## Decisions

### 1. Service Separation and Single Backend Image
- The FastAPI API and worker background processes share the same `backend/Dockerfile` (based on `python:3.11-slim`).
- The API runs `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- The worker runs `python -m app.workers.runtime`.
- Both execute as a non-root user (`appuser`, UID 10001) for security.
- Worker containers can be scaled independently using Compose's native `--scale worker=N` flag. Each container dynamically derives its worker identity from `socket.gethostname()` (the Docker container ID) ensuring distinct worker registrations and logs.

### 2. Multi-Stage Frontend Build & Nginx Serving
- The React/Vite SPA is built via a multi-stage Dockerfile (`node:22-alpine` builder $\rightarrow$ `nginx:1.27-alpine` runtime).
- `VITE_API_BASE_URL` is configured at build time (defaulting to browser-facing `http://localhost:8000`).
- Nginx provides client-side SPA routing fallback (`try_files $uri $uri/ /index.html`), gzip compression, and a dedicated `/healthz` health endpoint.

### 3. Container Networking & Service Discovery
- All 8 services (`postgres`, `redis`, `api`, `worker`, `frontend`, `prometheus`, `grafana`, `otel-collector`) join a dedicated bridge network (`dtp_network`).
- Internal communications use service names (`postgres:5432`, `redis:6379`, `api:8000`, `otel-collector:4317`).
- External ports are exposed for local developer access (`5173` for Frontend, `8000` for API, `9090` for Prometheus, `3000` for Grafana, `5432` for PostgreSQL, `6379` for Redis).

### 4. Health Checks and Startup Ordering
- **PostgreSQL**: `pg_isready -U postgres -d workflow_platform`
- **Redis**: `redis-cli ping`
- **API**: `curl -f http://localhost:8000/health/live` (process liveness).
- **Frontend**: `wget --spider http://localhost/healthz`
- `api` and `worker` depend on `postgres` and `redis` with `condition: service_healthy`. Worker containers depend directly on healthy infrastructure without coupling to the API container.

### 5. Explicit Database Migrations
- Automatic migration on container startup is deliberately avoided to prevent race conditions when scaling multiple API or worker instances.
- Migrations are applied explicitly via `docker compose exec api alembic upgrade head` or `docker compose run --rm api alembic upgrade head`.

### 6. Durable Volume Management
- PostgreSQL state persists in the named volume `postgres_data` (`dtp_postgres_data`).
- Stopping the stack (`docker compose down`) preserves all users, projects, tasks, and workflows.
- Destroying volumes requires an intentional `docker compose down -v`.

## Consequences

- **Reproducibility**: Any developer or test environment can boot the complete platform with `docker compose up --build`.
- **Horizontal Scaling**: Adding workers is as simple as `--scale worker=N` without architectural or code changes.
- **Safety**: Process liveness, database connectivity, and telemetry are verified automatically via healthchecks.
