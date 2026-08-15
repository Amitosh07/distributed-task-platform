"""Image resize handler.

Payload schema:
    {
        "image_b64": "<base64-encoded image bytes>",
        "width":  <int, 1..4096>,
        "height": <int, 1..4096>,
        "format": "<optional output format: 'JPEG' | 'PNG' | 'WEBP', default 'JPEG'>"
    }

Result schema:
    {
        "original_width": <int>,
        "original_height": <int>,
        "original_format": "<str>",
        "resized_width": <int>,
        "resized_height": <int>,
        "output_format": "<str>",
        "output_size_bytes": <int>
    }

Security:
- Input is base64-encoded bytes in the payload; no filesystem paths accepted.
- Dimensions are bounded to MAX_DIMENSION (4096 px) in each axis.
- Input image size is bounded to MAX_INPUT_BYTES (5 MB) to prevent memory attacks.
- Pillow is used in a memory-safe way; the resized bytes are NOT stored in Redis.
- The result only contains metadata; the resized image is discarded after the
  handler returns. In a production system the caller would supply a destination
  (e.g. object storage key) and the handler would upload there.
"""

import base64
import binascii
import io
from typing import Any

from PIL import Image, UnidentifiedImageError

MAX_DIMENSION = 4096
MAX_INPUT_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


def image_resize_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate, decode, and resize the supplied image; return metadata."""
    # --- Validate inputs ---
    image_b64 = payload.get("image_b64")
    if image_b64 is None:
        raise ValueError("payload must include 'image_b64'")
    if not isinstance(image_b64, str):
        raise ValueError("'image_b64' must be a base64-encoded string")

    width = payload.get("width")
    height = payload.get("height")
    if width is None or height is None:
        raise ValueError("payload must include 'width' and 'height'")
    if not isinstance(width, int) or not isinstance(height, int):
        raise ValueError("'width' and 'height' must be integers")
    if not (1 <= width <= MAX_DIMENSION):
        raise ValueError(f"'width' must be between 1 and {MAX_DIMENSION}")
    if not (1 <= height <= MAX_DIMENSION):
        raise ValueError(f"'height' must be between 1 and {MAX_DIMENSION}")

    output_format = payload.get("format", "JPEG").upper()
    if output_format not in ALLOWED_FORMATS:
        raise ValueError(f"'format' must be one of {sorted(ALLOWED_FORMATS)}")

    # --- Decode base64 ---
    try:
        image_bytes = base64.b64decode(image_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"'image_b64' is not valid base64: {exc}") from exc

    if len(image_bytes) > MAX_INPUT_BYTES:
        raise ValueError(
            f"Decoded image size ({len(image_bytes)} bytes) exceeds the "
            f"maximum of {MAX_INPUT_BYTES} bytes"
        )

    # --- Open and resize ---
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.verify()  # Check for truncated / corrupt data
    except UnidentifiedImageError as exc:
        raise ValueError(f"Cannot identify image format: {exc}") from exc
    except Exception as exc:
        raise ValueError(f"Image validation failed: {exc}") from exc

    # Re-open after verify() (verify() leaves the file pointer in an unusable state)
    img = Image.open(io.BytesIO(image_bytes))
    original_width, original_height = img.size
    original_format = img.format or "UNKNOWN"

    resized = img.resize((width, height), Image.LANCZOS)

    # Write to an in-memory buffer to get output size; NOT stored in Redis.
    output_buffer = io.BytesIO()
    save_format = "JPEG" if output_format == "JPEG" else output_format
    resized.save(output_buffer, format=save_format)
    output_size_bytes = output_buffer.tell()

    return {
        "original_width": original_width,
        "original_height": original_height,
        "original_format": original_format,
        "resized_width": width,
        "resized_height": height,
        "output_format": output_format,
        "output_size_bytes": output_size_bytes,
    }
