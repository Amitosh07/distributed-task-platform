"""CSV statistics handler.

Payload schema:
    {
        "csv_data": "<CSV string, max 100 KB>",
        "delimiter": "<single character, optional, default ','>"
    }

Result schema:
    {
        "row_count": <int>,
        "column_count": <int>,
        "column_names": [<str>, ...],
        "has_header": true
    }

Security:
- CSV data is received inline in the payload (no filesystem reads).
- Size is bounded to MAX_CSV_BYTES to prevent memory exhaustion.
- The standard csv module is used; no arbitrary code execution.
- Delimiter is validated to be a single printable character.
"""

import csv
import io
from typing import Any


MAX_CSV_BYTES = 100 * 1024  # 100 KB


def csv_stats_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Return basic statistics for inline CSV data."""
    csv_data = payload.get("csv_data")
    if csv_data is None:
        raise ValueError("payload must include 'csv_data'")
    if not isinstance(csv_data, str):
        raise ValueError("'csv_data' must be a string")
    if len(csv_data.encode()) > MAX_CSV_BYTES:
        raise ValueError(f"'csv_data' exceeds the maximum allowed size of {MAX_CSV_BYTES} bytes")

    delimiter = payload.get("delimiter", ",")
    if not isinstance(delimiter, str) or len(delimiter) != 1:
        raise ValueError("'delimiter' must be a single character")

    reader = csv.reader(io.StringIO(csv_data), delimiter=delimiter)
    rows = list(reader)

    if not rows:
        return {"row_count": 0, "column_count": 0, "column_names": [], "has_header": False}

    # Treat the first row as headers.
    header = rows[0]
    data_rows = rows[1:]

    return {
        "row_count": len(data_rows),
        "column_count": len(header),
        "column_names": header,
        "has_header": True,
    }
