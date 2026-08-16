"""Phase 9 — Containerization, Docker Compose & Health Checks Unit Tests.

These tests validate the Dockerfiles, Docker Compose configuration,
Nginx SPA routing, environment templates, and health check definitions
without requiring a live Docker daemon.
"""

from __future__ import annotations

import re
from pathlib import Path
import pytest

_ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
_BACKEND_DIR = _ROOT_DIR / "backend"
_FRONTEND_DIR = _ROOT_DIR / "frontend"
_COMPOSE_FILE = _ROOT_DIR / "docker-compose.yml"
_ENV_EXAMPLE = _ROOT_DIR / ".env.example"


# ---------------------------------------------------------------------------
# Helper to read text files
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    assert path.exists(), f"File does not exist: {path}"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Test Docker Compose Configuration
# ---------------------------------------------------------------------------

class TestDockerComposeConfig:
    """Validate structure and configuration of docker-compose.yml."""

    @pytest.fixture(scope="class")
    def compose_text(self) -> str:
        return _read(_COMPOSE_FILE)

    def test_compose_file_exists(self):
        assert _COMPOSE_FILE.exists()

    def test_all_eight_services_present(self, compose_text: str):
        expected_services = [
            "postgres:",
            "redis:",
            "api:",
            "worker:",
            "frontend:",
            "prometheus:",
            "grafana:",
            "otel-collector:",
        ]
        for service in expected_services:
            assert service in compose_text, f"Missing service definition: {service}"

    def test_healthchecks_configured_on_core_services(self, compose_text: str):
        assert "pg_isready" in compose_text, "PostgreSQL healthcheck missing"
        assert "redis-cli" in compose_text and "ping" in compose_text, "Redis healthcheck missing"
        assert "/health/live" in compose_text, "API liveness healthcheck missing"
        assert "/healthz" in compose_text or "wget" in compose_text, "Frontend healthcheck missing"

    def test_worker_depends_on_healthy_postgres_and_redis(self, compose_text: str):
        """Worker must depend directly on healthy infrastructure without coupling to API."""
        # Find worker block
        worker_block = compose_text.split("worker:")[1].split("frontend:")[0]
        assert "postgres:" in worker_block
        assert "condition: service_healthy" in worker_block
        assert "redis:" in worker_block
        # Worker should not depend on API
        assert "api:" not in worker_block.split("depends_on:")[1].split("networks:")[0]

    def test_named_volumes_declared(self, compose_text: str):
        assert "postgres_data:" in compose_text
        assert "redis_data:" in compose_text

    def test_network_declared(self, compose_text: str):
        assert "dtp_network:" in compose_text

    def test_grafana_uses_environment_credentials(self, compose_text: str):
        """Grafana credentials must come from environment variables."""
        grafana_block = compose_text.split("\n  grafana:\n")[1].split("\n  otel-collector:\n")[0]
        assert "GF_SECURITY_ADMIN_USER" in grafana_block
        assert "GF_SECURITY_ADMIN_PASSWORD" in grafana_block
        assert "${GF_SECURITY_ADMIN_PASSWORD" in grafana_block

    def test_frontend_vite_api_base_url_is_browser_facing(self, compose_text: str):
        """Frontend build arg VITE_API_BASE_URL must default to browser-facing localhost:8000."""
        frontend_block = compose_text.split("frontend:")[1].split("prometheus:")[0]
        assert "VITE_API_BASE_URL" in frontend_block
        assert "http://localhost:8000" in frontend_block
        # Must never use container-internal http://api:8000
        assert "http://api:8000" not in frontend_block


# ---------------------------------------------------------------------------
# Test Backend Dockerfile & .dockerignore
# ---------------------------------------------------------------------------

class TestBackendDockerfile:
    """Validate backend Dockerfile best practices."""

    @pytest.fixture(scope="class")
    def dockerfile_text(self) -> str:
        return _read(_BACKEND_DIR / "Dockerfile")

    def test_dockerfile_exists(self):
        assert (_BACKEND_DIR / "Dockerfile").exists()

    def test_dockerignore_exists(self):
        assert (_BACKEND_DIR / ".dockerignore").exists()

    def test_uses_slim_python_image(self, dockerfile_text: str):
        assert "FROM python:3.11-slim" in dockerfile_text or "FROM python:3.12-slim" in dockerfile_text

    def test_runs_as_non_root_user(self, dockerfile_text: str):
        assert "useradd" in dockerfile_text or "adduser" in dockerfile_text
        assert "USER appuser" in dockerfile_text

    def test_installs_curl_for_healthchecks(self, dockerfile_text: str):
        assert "curl" in dockerfile_text

    def test_healthcheck_instruction_present(self, dockerfile_text: str):
        assert "HEALTHCHECK" in dockerfile_text
        assert "/health/live" in dockerfile_text

    def test_copies_migration_files(self, dockerfile_text: str):
        assert "alembic" in dockerfile_text
        assert "alembic.ini" in dockerfile_text


# ---------------------------------------------------------------------------
# Test Frontend Dockerfile, nginx.conf & .dockerignore
# ---------------------------------------------------------------------------

class TestFrontendDockerfile:
    """Validate frontend Dockerfile and Nginx configuration."""

    @pytest.fixture(scope="class")
    def dockerfile_text(self) -> str:
        return _read(_FRONTEND_DIR / "Dockerfile")

    @pytest.fixture(scope="class")
    def nginx_text(self) -> str:
        return _read(_FRONTEND_DIR / "nginx.conf")

    def test_dockerfile_exists(self):
        assert (_FRONTEND_DIR / "Dockerfile").exists()

    def test_dockerignore_exists(self):
        assert (_FRONTEND_DIR / ".dockerignore").exists()

    def test_nginx_conf_exists(self):
        assert (_FRONTEND_DIR / "nginx.conf").exists()

    def test_multi_stage_build(self, dockerfile_text: str):
        assert "AS build" in dockerfile_text
        assert "AS runtime" in dockerfile_text or "FROM nginx" in dockerfile_text

    def test_nginx_conf_has_spa_fallback(self, nginx_text: str):
        assert "try_files $uri $uri/ /index.html;" in nginx_text

    def test_nginx_conf_has_health_endpoint(self, nginx_text: str):
        assert "/healthz" in nginx_text

    def test_frontend_healthcheck_instruction_present(self, dockerfile_text: str):
        assert "HEALTHCHECK" in dockerfile_text


# ---------------------------------------------------------------------------
# Test Environment Templates & ADR
# ---------------------------------------------------------------------------

class TestEnvironmentAndDocumentation:
    """Validate .env.example and Phase 9 ADR."""

    def test_env_example_exists_and_contains_required_keys(self):
        env_text = _read(_ENV_EXAMPLE)
        required_keys = [
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "POSTGRES_DB",
            "DATABASE_URL",
            "REDIS_URL",
            "JWT_SECRET_KEY",
            "VITE_API_BASE_URL",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "GF_SECURITY_ADMIN_USER",
            "GF_SECURITY_ADMIN_PASSWORD",
        ]
        for key in required_keys:
            assert f"{key}=" in env_text, f"Missing required env key: {key}"

    def test_vite_api_base_url_in_env_example_is_localhost(self):
        env_text = _read(_ENV_EXAMPLE)
        assert "VITE_API_BASE_URL=http://localhost:8000" in env_text
        assert "http://api:8000" not in env_text

    def test_adr_012_exists(self):
        adr_path = _ROOT_DIR / "docs" / "adr" / "ADR-012-phase9-containerization.md"
        assert adr_path.exists()
        adr_text = _read(adr_path)
        assert "ADR-012" in adr_text
        assert "Accepted" in adr_text
