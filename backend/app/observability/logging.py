"""Small JSON logger that deliberately accepts explicit safe fields only."""
import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", "platform"),
            "event": getattr(record, "event", record.name),
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        for key in ("task_id", "task_type", "worker_id", "workflow_run_id", "workflow_node_id", "status", "route", "error"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = str(value)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def log_event(logger: logging.Logger, level: int, event: str, message: str, *, service: str, **fields: object) -> None:
    """Emit structured fields; callers must never pass secrets or raw payloads."""
    logger.log(level, message, extra={"event": event, "service": service, **fields})
