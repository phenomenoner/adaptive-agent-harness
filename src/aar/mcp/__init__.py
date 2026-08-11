"""MCP adapter for the Adaptive Agent Runtime."""

from pathlib import Path
from typing import Any, Literal


def build_server(
    database_path: Path,
    *,
    now_ms: Any = None,
    programmable_backend: Literal["plain", "ipython"] = "ipython",
):
    """Build the MCP application without importing the executable module eagerly."""

    from aar.mcp.server import build_server as _build_server

    options = {"programmable_backend": programmable_backend}
    if now_ms is not None:
        options["now_ms"] = now_ms
    return _build_server(database_path, **options)

__all__ = ["build_server"]
