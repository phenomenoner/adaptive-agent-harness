"""Public runtime components for reference and programmable workspace lifecycles."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aar.rlm_models import RlmJobSnapshot, RlmJobSpec, RlmResult

if TYPE_CHECKING:
    from aar.runtime.ipython_backend import SupervisedIPythonWorkspaceBackend
    from aar.runtime.programming import PlainPythonWorkspaceBackend, WorkspaceBackend
    from aar.runtime.reference_host import ReferenceHost

__all__ = [
    "PlainPythonWorkspaceBackend",
    "ReferenceHost",
    "RlmJobSnapshot",
    "RlmJobSpec",
    "RlmResult",
    "SupervisedIPythonWorkspaceBackend",
    "WorkspaceBackend",
]


def __getattr__(name: str) -> Any:
    if name == "SupervisedIPythonWorkspaceBackend":
        from aar.runtime.ipython_backend import SupervisedIPythonWorkspaceBackend

        return SupervisedIPythonWorkspaceBackend
    if name in {"PlainPythonWorkspaceBackend", "WorkspaceBackend"}:
        from aar.runtime.programming import PlainPythonWorkspaceBackend, WorkspaceBackend

        return {
            "PlainPythonWorkspaceBackend": PlainPythonWorkspaceBackend,
            "WorkspaceBackend": WorkspaceBackend,
        }[name]
    if name == "ReferenceHost":
        from aar.runtime.reference_host import ReferenceHost

        return ReferenceHost
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
