"""Queue package — Redis-backed task dispatch.

Redis is used exclusively for queueing task IDs. PostgreSQL remains the
authoritative source of durable task state. No task results or large payloads
are stored in Redis.
"""
