"""Focused Phase 7 unit tests; they require no database, Redis, or collector."""
import json
import logging

from app.observability.logging import JsonFormatter, log_event, request_id_var
from app.observability.metrics import HTTP_REQUESTS, TASK_SUBMISSIONS, WORKFLOW_NODES
from app.observability.tracing import tracer


def test_metric_labels_are_cardinality_safe():
    for metric in (HTTP_REQUESTS, TASK_SUBMISSIONS, WORKFLOW_NODES):
        assert not ({"task_id", "workflow_id", "workflow_run_id", "request_id", "user_id"} & set(metric._labelnames))


def test_structured_log_has_request_id_and_no_implicit_secret_fields():
    request_id_var.set("request-test")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "task queued", (), None)
    record.service = "api"  # type: ignore[attr-defined]
    record.event = "task_submitted"  # type: ignore[attr-defined]
    record.task_id = "task-123"  # type: ignore[attr-defined]
    payload = json.loads(JsonFormatter().format(record))
    assert payload["request_id"] == "request-test"
    assert payload["task_id"] == "task-123"
    assert "authorization" not in payload and "password" not in payload


def test_tracer_is_available_without_exporter():
    assert tracer("phase7-test") is not None
