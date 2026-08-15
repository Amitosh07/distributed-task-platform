# API Design

## Conventions

All protected `/v1` endpoints require a bearer access token or API key and enforce project-level authorization. JSON error responses use a stable shape such as `{ "error": { "code", "message", "details" } }`. IDs are opaque strings. List endpoints support validated pagination and filtering. This is a contract only; no endpoints are implemented in Phase 0.

| Endpoint | Purpose, request/response, validation, and status codes |
|---|---|
| `POST /v1/auth/login` | **Auth:** none. Request `{email,password}`. Response `200` with short-lived access token, expiry, and user summary. Validate credentials/rate limits; errors: `400` malformed, `401` invalid credentials, `429` limited. |
| `POST /v1/tasks` | **Auth:** project client/developer. Request `project_id,type,payload,priority?,scheduled_at?,timeout_seconds?,max_retries?,idempotency_key?`. Response `201` `{task_id,status,created_at}`; an existing idempotency key returns the original logical task (`200` or documented idempotent `201`). Validate project access, fixed task type/schema, sizes, priority, future schedule, timeout/retry bounds. Errors `400`, `401`, `403`, `409` conflicting key payload, `422`, `503` controlled durable-store failure. |
| `GET /v1/tasks` | **Auth:** project member. Query `project_id,status?,type?,worker_id?,created_from?,created_to?,page?,page_size?`. Response `200` paginated task summaries. Validate filters/page bounds; errors `400`, `401`, `403`. |
| `GET /v1/tasks/{task_id}` | **Auth:** project member. No body. Response `200` task, status, timestamps, attempts, result/error summary, and event timeline reference. Validate ID and project access; errors `401`, `403`, `404`. |
| `POST /v1/tasks/{task_id}/cancel` | **Auth:** authorized project operator/client. Optional `{reason}`. Response `200` cancelled task or `202` if a later cooperative cancellation protocol is needed. Only `CREATED`/`QUEUED` are initially cancellable; invalid state is `409`; also `401`, `403`, `404`, `422`. |
| `POST /v1/tasks/{task_id}/retry` | **Auth:** project operator. Optional `{reason}`. Response `202` with queued/retry task summary. Validate terminal/eligible state and policy; no duplicate active attempt. Errors `401`, `403`, `404`, `409`, `422`. |
| `GET /v1/tasks/{task_id}/logs` | **Auth:** project member. Query `attempt?,cursor?,limit?`. Response `200` `{items,next_cursor}` with authorized task/attempt log entries. Validate retention/cursor and project access; errors `401`, `403`, `404`, `422`. |
| `POST /v1/workflows` | **Auth:** project developer/operator. Request `project_id,name,nodes,edges,failure_policy?`. Response `201` `{workflow_id,...}`. Validate node keys/types/payloads, same-project references, and acyclic graph; errors `400`, `401`, `403`, `409`, `422`. |
| `POST /v1/workflows/{id}/run` | **Auth:** project operator. Optional `{input, idempotency_key?}`. Response `202` `{run_id,status}`. Validate definition/project access and runnable state; errors `401`, `403`, `404`, `409`, `422`. |
| `GET /v1/workflows/{id}/runs/{run_id}` | **Auth:** project member. No body. Response `200` run status, timestamps, failure policy, node statuses/task references, and errors. Validate matching workflow/run and access; errors `401`, `403`, `404`. |
| `POST /v1/workers/register` | **Auth:** worker credential. Request `worker_id?,hostname,version,capabilities,concurrency_limit`. Response `201`/`200` worker registration and heartbeat interval. Validate identity, capability shape, positive concurrency, and allowed registration scope; errors `401`, `403`, `409`, `422`. |
| `POST /v1/workers/{id}/heartbeat` | **Auth:** that worker credential. Request `{status?,available_slots?,running_task_ids?,observed_at}`. Response `200` with accepted server time/lease guidance. Validate identity match, monotonic/sane timestamp and capacity; errors `401`, `403`, `404`, `409`, `422`. |
| `GET /v1/workers` | **Auth:** project/system operator. Query `status?,capability?,page?,page_size?`. Response `200` paginated worker health: heartbeat age, capacity, capabilities, status. Errors `401`, `403`, `422`. |
| `GET /health/live` | **Auth:** none or infrastructure-only. Response `200` if process is alive; no dependency check. `503` only when the process cannot serve liveness. |
| `GET /health/ready` | **Auth:** none or infrastructure-only. Response `200` only when required durable dependencies are reachable for service; otherwise `503` with non-sensitive readiness reason. |

Task responses must distinguish queue time, execution time, attempts, and durable status. API status responses are read from PostgreSQL; Redis queue observations are operational signals, not authority.
