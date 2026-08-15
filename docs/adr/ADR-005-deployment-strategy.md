# ADR-005: Deployment strategy

## Decision
Start with Docker Compose locally, then use a simple Linux VM/cloud deployment after local testing.

## Rationale
Compose makes the initial API, workers, PostgreSQL, Redis, frontend, and observability stack reproducible without operational complexity. Cloud follows a tested local system; a VM may use Caddy/Nginx, HTTPS, backups, and monitoring. Managed PostgreSQL/Redis are optional later.

## Consequences
There is no Kubernetes initially and no unnecessary microservices. Kafka is not introduced unless a future measured requirement justifies it.
