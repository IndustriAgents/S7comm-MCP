from __future__ import annotations

from typing import Any

OperationMeta = dict[str, Any]
MAX_BYTE_OFFSET = 2097151


class S7ClientError(RuntimeError):
    """Raised when an S7 operation fails."""


def describe_error(exc: BaseException) -> str:
    """Render a python-snap7 error (whose message is ``bytes``) as plain text."""
    if exc.args and isinstance(exc.args[0], (bytes, bytearray)):
        return bytes(exc.args[0]).decode("latin-1", errors="replace").strip()
    return str(exc).strip() or type(exc).__name__
