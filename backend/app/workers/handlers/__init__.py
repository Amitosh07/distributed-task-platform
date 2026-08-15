"""Handler registry for Phase 2 task execution.

IMPORTANT — Security design (ADR-006):
    Only handlers explicitly registered in HANDLERS are callable.
    The worker NEVER uses eval(), exec(), __import__(), subprocess with
    user input, or any dynamic import of arbitrary user-supplied code.
    Adding a new handler requires modifying this file intentionally.

Handler contract:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        # Raises ValueError for invalid input.
        # Raises any exception on execution failure.
        # Returns a JSON-serialisable dict on success.
        ...
"""

from typing import Any, Callable

from app.workers.handlers.csv_stats_handler import csv_stats_handler
from app.workers.handlers.http_check_handler import http_check_handler
from app.workers.handlers.image_resize_handler import image_resize_handler
from app.workers.handlers.sleep_handler import sleep_handler

HandlerFunc = Callable[[dict[str, Any]], dict[str, Any]]

# Explicit, static registry — no dynamic lookup of arbitrary Python code.
HANDLERS: dict[str, HandlerFunc] = {
    "sleep": sleep_handler,
    "csv_stats": csv_stats_handler,
    "image_resize": image_resize_handler,
    "http_check": http_check_handler,
}

__all__ = ["HANDLERS", "HandlerFunc"]
