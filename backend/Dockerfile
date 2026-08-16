# ===========================================================================
# Distributed Task Platform — Backend Dockerfile (API & Worker runtime)
# ===========================================================================

FROM python:3.11-slim AS runtime

# Install curl for Docker health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/bash -m appuser

WORKDIR /app

# Install dependencies first for optimal Docker layer caching
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application source and migration configuration
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .

# Set file ownership to non-root user
RUN chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

# Health check using the process liveness endpoint
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

# Default CMD runs the FastAPI application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
