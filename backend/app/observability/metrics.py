"""Cardinality-safe Prometheus metrics. Never add IDs as labels."""
from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status_code"])
HTTP_DURATION = Histogram("http_request_duration_seconds", "HTTP request duration", ["method", "route"])
TASK_SUBMISSIONS = Counter("task_submissions_total", "Tasks durably submitted", ["task_type"])
TASK_COMPLETIONS = Counter("task_completions_total", "Completed tasks", ["task_type", "status"])
TASK_FAILURES = Counter("task_failures_total", "Failed tasks", ["task_type", "status"])
TASK_RUNNING = Gauge("tasks_running", "Tasks currently running", ["task_type"])
TASK_EXECUTION = Histogram("task_execution_duration_seconds", "Task execution duration", ["task_type"])
TASK_QUEUE_WAIT = Histogram("task_queue_wait_duration_seconds", "Queue wait duration", ["task_type"])
TASK_RETRIES = Counter("task_retries_total", "Task retries scheduled", ["task_type"])
TASK_TIMEOUTS = Counter("task_timeouts_total", "Timed out tasks", ["task_type"])
WORKERS_STARTED = Counter("workers_started_total", "Workers started")
WORKER_HEARTBEATS = Counter("worker_heartbeats_total", "Worker heartbeats")
WORKER_CLAIMS = Counter("worker_tasks_claimed_total", "Tasks claimed by workers", ["task_type"])
WORKER_COMPLETIONS = Counter("worker_tasks_completed_total", "Tasks completed by workers", ["task_type"])
WORKER_FAILURES = Counter("worker_tasks_failed_total", "Tasks failed by workers", ["task_type"])
WORKER_RECOVERIES = Counter("worker_recoveries_total", "Recovered stale tasks")
STALE_WORKERS = Counter("stale_workers_detected_total", "Workers marked stale")
QUEUE_PUBLISHED = Counter("queue_publish_total", "Queue publishes")
QUEUE_PUBLISH_FAILURES = Counter("queue_publish_failures_total", "Queue publish failures")
QUEUE_CONSUMED = Counter("queue_consume_total", "Queue messages consumed")
QUEUE_CONSUME_FAILURES = Counter("queue_consume_failures_total", "Queue consume failures")
QUEUE_DEPTH = Gauge("queue_depth", "Last observed Redis queue depth")
WORKFLOW_STARTED = Counter("workflow_runs_started_total", "Workflow runs started", ["failure_policy"])
WORKFLOW_COMPLETED = Counter("workflow_runs_completed_total", "Workflow runs completed", ["status", "failure_policy"])
WORKFLOW_DURATION = Histogram("workflow_run_duration_seconds", "Workflow run duration", ["failure_policy"])
WORKFLOW_NODES = Counter("workflow_nodes_total", "Workflow node lifecycle events", ["task_type", "status"])


def refresh_queue_depth() -> None:
    """One Redis read per Prometheus scrape, never per task operation."""
    try:
        from app.queue.publisher import QUEUE_NAME
        from app.queue.redis_client import get_redis_client
        QUEUE_DEPTH.set(get_redis_client().llen(QUEUE_NAME))
    except Exception:
        # Metrics must never make Redis or task execution unavailable.
        return
