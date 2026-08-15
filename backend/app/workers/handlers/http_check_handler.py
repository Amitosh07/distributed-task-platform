"""HTTP check handler.

Payload schema:
    {
        "url": "<http:// or https:// URL>",
        "timeout_seconds": <int | float, optional, default 10, max 30>
    }

Result schema:
    {
        "url": "<checked URL>",
        "status_code": <int>,
        "reason": "<str>",
        "elapsed_ms": <float>,
        "reachable": <bool>
    }

Security considerations and limitations:
- Only http:// and https:// schemes are accepted; ftp://, file://, etc. are rejected.
- Private/internal IP ranges (RFC 1918, loopback, link-local, ULA) are BLOCKED to
  mitigate Server-Side Request Forgery (SSRF). This is enforced by resolving the
  hostname and checking against forbidden ranges before connecting.
- Request timeout is bounded to MAX_TIMEOUT_SECONDS (30 s) to prevent unbounded waits.
- Redirects are NOT followed automatically to avoid redirect-based SSRF bypasses.
- This is a best-effort SSRF mitigation for Phase 2; a production system should add
  DNS rebinding protection and an egress allowlist.
- No credentials, cookies, or custom headers from the user payload are forwarded.
"""

import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

MAX_TIMEOUT_SECONDS = 30
DEFAULT_TIMEOUT_SECONDS = 10

# RFC 1918 private, loopback, link-local, ULA, and documentation ranges
_FORBIDDEN_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
]


def _is_private(hostname: str) -> bool:
    """Return True if *hostname* resolves to a private/internal IP address."""
    try:
        resolved = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # Cannot resolve — treat as safe to fail at connection time.
        return False
    for _family, _type, _proto, _canonname, sockaddr in resolved:
        try:
            addr = ipaddress.ip_address(sockaddr[0])
            if any(addr in net for net in _FORBIDDEN_NETWORKS):
                return True
        except ValueError:
            pass
    return False


def http_check_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Perform an HTTP GET to *url* and return status information."""
    url = payload.get("url")
    if url is None:
        raise ValueError("payload must include 'url'")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("'url' must be a non-empty string")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("'url' must use the http or https scheme")
    if not parsed.hostname:
        raise ValueError("'url' must include a hostname")

    # SSRF mitigation: block private IP ranges
    if _is_private(parsed.hostname):
        raise ValueError(
            f"'url' hostname '{parsed.hostname}' resolves to a private or "
            "internal IP address and is not permitted"
        )

    timeout_seconds = payload.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout_seconds, (int, float)):
        raise ValueError("'timeout_seconds' must be a number")
    timeout_seconds = float(timeout_seconds)
    if timeout_seconds <= 0 or timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise ValueError(
            f"'timeout_seconds' must be between 0 and {MAX_TIMEOUT_SECONDS}"
        )

    logger.info("http_check: GET %s (timeout=%.1fs)", url, timeout_seconds)
    try:
        with httpx.Client(follow_redirects=False, timeout=timeout_seconds) as client:
            response = client.get(url)
        elapsed_ms = response.elapsed.total_seconds() * 1000
        return {
            "url": url,
            "status_code": response.status_code,
            "reason": response.reason_phrase,
            "elapsed_ms": round(elapsed_ms, 2),
            "reachable": True,
        }
    except httpx.TimeoutException as exc:
        logger.warning("http_check: timeout reaching %s: %s", url, exc)
        return {
            "url": url,
            "status_code": None,
            "reason": "timeout",
            "elapsed_ms": None,
            "reachable": False,
        }
    except httpx.RequestError as exc:
        logger.warning("http_check: request error for %s: %s", url, exc)
        return {
            "url": url,
            "status_code": None,
            "reason": str(exc),
            "elapsed_ms": None,
            "reachable": False,
        }
