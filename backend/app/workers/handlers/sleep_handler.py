"""Sleep handler — primary handler for demonstrating async task execution.

Payload schema:
    {
        "seconds": <int | float, 0 < seconds <= 300>
    }

Result schema:
    {
        "message": "slept successfully",
        "seconds": <number actually slept>
    }

Security: no external calls, no filesystem access, bounded sleep duration.
"""

import time
from typing import Any


MAX_SLEEP_SECONDS = 300


def sleep_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Sleep for the requested duration and return a structured result."""
    seconds = payload.get("seconds")
    if seconds is None:
        raise ValueError("payload must include 'seconds'")
    if not isinstance(seconds, (int, float)):
        raise ValueError("'seconds' must be a number")
    seconds = float(seconds)
    if seconds <= 0:
        raise ValueError("'seconds' must be greater than 0")
    if seconds > MAX_SLEEP_SECONDS:
        raise ValueError(f"'seconds' must be at most {MAX_SLEEP_SECONDS} (got {seconds})")

    time.sleep(seconds)
    return {"message": "slept successfully", "seconds": seconds}
