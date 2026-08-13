"""MCP adapter for the Adaptive Agent Runtime."""

from pathlib import Path
from typing import Any, Literal


def build_server(
    database_path: Path,
    *,
    now_ms: Any = None,
    programmable_backend: Literal["plain", "ipython"] = "ipython",
    model_broker_registry: Any = None,
    default_model_route_profile: str | None = None,
):
    """Build the MCP application without importing the executable module eagerly."""

    from aar.mcp.server import build_server as _build_server

    options = {
        "programmable_backend": programmable_backend,
        "model_broker_registry": model_broker_registry,
        "default_model_route_profile": default_model_route_profile,
    }
    if now_ms is not None:
        options["now_ms"] = now_ms
    return _build_server(database_path, **options)

__all__ = ["build_server"]
