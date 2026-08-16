# ADR-011: Phase 7 Observability

## Decision

Use in-process Prometheus instrumentation, JSON stdout logs, `X-Request-ID` correlation, and optional OpenTelemetry export. Prometheus and Grafana run as local Compose services and do not participate in task durability or execution.

## Cardinality policy

Metrics use only bounded labels: task type, terminal status, failure policy, HTTP method, and route templates. They never use task, worker, user, request, workflow, run, or node identifiers. Those identifiers belong in structured logs and traces.

## Consequences

If Prometheus, Grafana, or an OTLP collector is down, tasks still persist and execute. API and worker processes retain their normal PostgreSQL/Redis behavior; telemetry export failures are logged rather than propagated.
