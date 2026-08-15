"""Phase 2 — handler unit tests.

All handler tests are pure unit tests: no Redis, no PostgreSQL, no network
(http_check network calls are mocked).  They run fast and without any
infrastructure dependency.

Tests cover:
- sleep_handler: valid, zero, negative, too-long, missing field
- csv_stats_handler: valid, oversized, empty, custom delimiter, missing field
- image_resize_handler: valid, invalid dimensions, oversized, bad base64, bad format
- http_check_handler: valid URL, private IP blocked, bad scheme, timeout (mocked)
"""

import base64
import io
import os
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from app.workers.handlers.csv_stats_handler import csv_stats_handler, MAX_CSV_BYTES
from app.workers.handlers.http_check_handler import http_check_handler
from app.workers.handlers.image_resize_handler import image_resize_handler, MAX_DIMENSION
from app.workers.handlers.sleep_handler import sleep_handler, MAX_SLEEP_SECONDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_png_b64(width: int = 10, height: int = 10) -> str:
    """Return a base64-encoded minimal PNG image."""
    img = Image.new("RGB", (width, height), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------
# sleep_handler
# ---------------------------------------------------------------------------

class TestSleepHandler:
    def test_valid_sleep(self):
        with patch("app.workers.handlers.sleep_handler.time.sleep") as mock_sleep:
            result = sleep_handler({"seconds": 5})
        mock_sleep.assert_called_once_with(5.0)
        assert result == {"message": "slept successfully", "seconds": 5.0}

    def test_float_seconds(self):
        with patch("app.workers.handlers.sleep_handler.time.sleep"):
            result = sleep_handler({"seconds": 0.5})
        assert result["seconds"] == 0.5

    def test_zero_seconds_raises(self):
        with pytest.raises(ValueError, match="greater than 0"):
            sleep_handler({"seconds": 0})

    def test_negative_seconds_raises(self):
        with pytest.raises(ValueError, match="greater than 0"):
            sleep_handler({"seconds": -1})

    def test_exceeds_maximum_raises(self):
        with pytest.raises(ValueError, match=str(MAX_SLEEP_SECONDS)):
            sleep_handler({"seconds": MAX_SLEEP_SECONDS + 1})

    def test_missing_seconds_raises(self):
        with pytest.raises(ValueError, match="'seconds'"):
            sleep_handler({})

    def test_non_numeric_seconds_raises(self):
        with pytest.raises(ValueError, match="number"):
            sleep_handler({"seconds": "five"})


# ---------------------------------------------------------------------------
# csv_stats_handler
# ---------------------------------------------------------------------------

class TestCsvStatsHandler:
    def test_valid_csv(self):
        result = csv_stats_handler({"csv_data": "name,age\nAlice,30\nBob,25"})
        assert result["row_count"] == 2
        assert result["column_count"] == 2
        assert result["column_names"] == ["name", "age"]
        assert result["has_header"] is True

    def test_empty_csv(self):
        result = csv_stats_handler({"csv_data": ""})
        assert result["row_count"] == 0
        assert result["column_count"] == 0

    def test_header_only_csv(self):
        result = csv_stats_handler({"csv_data": "col1,col2,col3"})
        assert result["row_count"] == 0
        assert result["column_count"] == 3
        assert result["column_names"] == ["col1", "col2", "col3"]

    def test_custom_delimiter(self):
        result = csv_stats_handler({"csv_data": "a;b;c\n1;2;3", "delimiter": ";"})
        assert result["column_count"] == 3

    def test_oversized_csv_raises(self):
        # Generate a string that clearly exceeds MAX_CSV_BYTES when encoded.
        large = "a," * (MAX_CSV_BYTES + 1)  # well over 100 KB
        with pytest.raises(ValueError, match="maximum allowed size"):
            csv_stats_handler({"csv_data": large})

    def test_missing_csv_data_raises(self):
        with pytest.raises(ValueError, match="'csv_data'"):
            csv_stats_handler({})

    def test_non_string_csv_raises(self):
        with pytest.raises(ValueError, match="string"):
            csv_stats_handler({"csv_data": 42})

    def test_invalid_delimiter_raises(self):
        with pytest.raises(ValueError, match="'delimiter'"):
            csv_stats_handler({"csv_data": "a,b", "delimiter": "too_long"})


# ---------------------------------------------------------------------------
# image_resize_handler
# ---------------------------------------------------------------------------

class TestImageResizeHandler:
    def test_valid_resize(self):
        result = image_resize_handler({
            "image_b64": _make_png_b64(100, 100),
            "width": 50,
            "height": 50,
        })
        assert result["resized_width"] == 50
        assert result["resized_height"] == 50
        assert result["original_width"] == 100
        assert result["original_height"] == 100
        assert result["output_format"] == "JPEG"
        assert result["output_size_bytes"] > 0

    def test_png_output_format(self):
        result = image_resize_handler({
            "image_b64": _make_png_b64(),
            "width": 5,
            "height": 5,
            "format": "PNG",
        })
        assert result["output_format"] == "PNG"

    def test_missing_image_b64_raises(self):
        with pytest.raises(ValueError, match="'image_b64'"):
            image_resize_handler({"width": 10, "height": 10})

    def test_missing_dimensions_raises(self):
        with pytest.raises(ValueError, match="'width' and 'height'"):
            image_resize_handler({"image_b64": _make_png_b64()})

    def test_dimension_too_large_raises(self):
        with pytest.raises(ValueError, match=str(MAX_DIMENSION)):
            image_resize_handler({
                "image_b64": _make_png_b64(),
                "width": MAX_DIMENSION + 1,
                "height": 10,
            })

    def test_zero_dimension_raises(self):
        with pytest.raises(ValueError, match="between 1"):
            image_resize_handler({
                "image_b64": _make_png_b64(),
                "width": 0,
                "height": 10,
            })

    def test_bad_base64_raises(self):
        with pytest.raises(ValueError, match="base64"):
            image_resize_handler({"image_b64": "not-valid-base64!!!", "width": 10, "height": 10})

    def test_bad_image_data_raises(self):
        valid_b64 = base64.b64encode(b"this is not an image").decode()
        with pytest.raises(ValueError):
            image_resize_handler({"image_b64": valid_b64, "width": 10, "height": 10})

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="'format'"):
            image_resize_handler({
                "image_b64": _make_png_b64(),
                "width": 10,
                "height": 10,
                "format": "BMP",
            })


# ---------------------------------------------------------------------------
# http_check_handler
# ---------------------------------------------------------------------------

class TestHttpCheckHandler:
    def _mock_response(self, status_code: int = 200, reason: str = "OK", elapsed_ms: float = 50.0):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.reason_phrase = reason
        elapsed = MagicMock()
        elapsed.total_seconds.return_value = elapsed_ms / 1000
        mock_resp.elapsed = elapsed
        return mock_resp

    def test_valid_url_success(self):
        with patch("app.workers.handlers.http_check_handler.httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = self._mock_response(200, "OK", 42.0)
            mock_client_cls.return_value = mock_ctx

            result = http_check_handler({"url": "https://example.com"})

        assert result["status_code"] == 200
        assert result["reachable"] is True
        assert result["url"] == "https://example.com"

    def test_http_scheme_allowed(self):
        with patch("app.workers.handlers.http_check_handler.httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = self._mock_response(200)
            mock_client_cls.return_value = mock_ctx
            result = http_check_handler({"url": "http://example.com"})
        assert result["reachable"] is True

    def test_ftp_scheme_rejected(self):
        with pytest.raises(ValueError, match="http or https"):
            http_check_handler({"url": "ftp://example.com"})

    def test_file_scheme_rejected(self):
        with pytest.raises(ValueError, match="http or https"):
            http_check_handler({"url": "file:///etc/passwd"})

    def test_private_ip_blocked(self):
        """Requests to RFC 1918 addresses must be blocked (SSRF mitigation)."""
        with patch("app.workers.handlers.http_check_handler.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("192.168.1.1", 80))]
            with pytest.raises(ValueError, match="private or internal"):
                http_check_handler({"url": "http://internal.example.com"})

    def test_loopback_blocked(self):
        with patch("app.workers.handlers.http_check_handler.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("127.0.0.1", 80))]
            with pytest.raises(ValueError, match="private or internal"):
                http_check_handler({"url": "http://localhost"})

    def test_timeout_returns_unreachable(self):
        import httpx
        with patch("app.workers.handlers.http_check_handler.httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.side_effect = httpx.TimeoutException("timed out")
            mock_client_cls.return_value = mock_ctx

            result = http_check_handler({"url": "https://example.com"})
        assert result["reachable"] is False
        assert result["reason"] == "timeout"

    def test_missing_url_raises(self):
        with pytest.raises(ValueError, match="'url'"):
            http_check_handler({})

    def test_timeout_exceeds_maximum_raises(self):
        with pytest.raises(ValueError, match="timeout_seconds"):
            http_check_handler({"url": "https://example.com", "timeout_seconds": 999})

    def test_invalid_timeout_type_raises(self):
        with pytest.raises(ValueError, match="number"):
            http_check_handler({"url": "https://example.com", "timeout_seconds": "fast"})
