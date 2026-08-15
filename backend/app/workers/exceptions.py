"""Worker and task execution exceptions."""


class TaskExecutionError(Exception):
    """Base exception for task execution failures."""


class NonRetryableError(TaskExecutionError):
    """Exception indicating a permanent failure that should not be retried.

    Examples: schema validation failure, unsupported task type, security rejection.
    """


class TaskTimeoutError(TaskExecutionError):
    """Exception raised when a task exceeds its configured timeout."""
