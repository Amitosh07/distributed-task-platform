"""Retry policy and backoff calculation.

Rules:
- max_retries represents additional attempts after the initial execution.
  Total allowed attempts = 1 + max_retries.
- Non-retryable errors (e.g. ValueError, NonRetryableError, schema errors) fail immediately.
- Retryable errors (e.g. TaskTimeoutError, transient runtime errors) retry if attempts remain.
- Exponential backoff is calculated as: min(max_seconds, base_seconds * (2 ** (attempt_count - 1))).
"""

from app.workers.exceptions import NonRetryableError


def is_retryable_error(exc: Exception) -> bool:
    """Determine whether an exception qualifies for automatic retry."""
    if isinstance(exc, (NonRetryableError, ValueError, TypeError)):
        return False
    return True


def calculate_backoff_delay(
    attempt_count: int,
    base_seconds: float = 1.0,
    max_seconds: float = 60.0,
) -> float:
    """Calculate exponential backoff delay for the given attempt count.

    Attempt 1 failure (before attempt 2) -> base_seconds * (2 ** 0) = base_seconds.
    Attempt 2 failure (before attempt 3) -> base_seconds * (2 ** 1) = 2 * base_seconds.
    """
    if attempt_count <= 0:
        return 0.0
    delay = base_seconds * (2 ** (attempt_count - 1))
    return min(max_seconds, max(0.0, delay))
