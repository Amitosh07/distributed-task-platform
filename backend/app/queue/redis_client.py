"""Centralised Redis connection management.

All Redis access in this application goes through this module.
No other module imports redis directly; this keeps connection lifecycle
management in a single place and makes it easy to swap in a pool or
test double.

The queue uses Redis DB 0 in development (REDIS_URL=redis://localhost:6379/0).
Tests use DB 1 (TEST_REDIS_URL=redis://localhost:6379/1) to avoid interfering
with the development queue.
"""

import logging

import redis

from app.config import get_settings

logger = logging.getLogger(__name__)

# Module-level pool so every import shares connections.
_pool: redis.ConnectionPool | None = None


def _get_pool() -> redis.ConnectionPool:
    """Return the shared connection pool, creating it on first call."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = redis.ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=10,
        )
        logger.info("Redis connection pool created: %s", settings.redis_url)
    return _pool


def get_redis_client() -> redis.Redis:
    """Return a Redis client backed by the shared connection pool.

    The caller must not close the pool; individual connections are returned
    to the pool automatically after each command.
    """
    return redis.Redis(connection_pool=_get_pool())


def check_redis_health() -> bool:
    """Ping Redis and return True if reachable, False otherwise.

    Used by the readiness health check. Never raises; failures are logged.
    """
    try:
        client = get_redis_client()
        return client.ping()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis health check failed: %s", exc)
        return False
