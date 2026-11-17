"""Ephemeral helper-origin metadata and the existing native cell JSON carrier.

No new public wire fields, durable state or publication authority. The IPC decoder
marks a validated result in memory; only native cell persistence uses the reserved
carrier. Generic workspace consumers retain their original JSON result values.
Host Python with arbitrary collaborator/DB access is not a security boundary here.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import PrivateAttr

from aar.runtime.workspace_models import WorkspaceProgramResult


class _HelperResult(WorkspaceProgramResult):
    _completion: dict[str, Any] | None = PrivateAttr(default=None)


def project_worker_completion(
    result: WorkspaceProgramResult,
    completion: Any,
) -> WorkspaceProgramResult:
    if completion is None or result.status != "succeeded":
        return result
    if not isinstance(completion, dict):
        raise ValueError("invalid worker completion projection")
    # Copy an already validated result. Private metadata is not a public field and
    # survives in-memory receipt/model_copy paths until native cell persistence.
    projected = _HelperResult.model_construct(**result.__dict__)
    projected._completion = deepcopy(completion)
    return projected


def workbench_result_document(result: WorkspaceProgramResult) -> dict[str, Any]:
    document = result.model_dump(mode="json")
    if isinstance(result, _HelperResult) and result._completion is not None:
        document["result"] = {"__aar_completion_v1__": deepcopy(result._completion)}
    elif isinstance(document.get("result"), dict) and "__aar_completion_v1__" in document["result"]:
        # Escape only the native persistence carrier, not generic result data.
        document["result"] = {"__aar_ordinary_v1__": document["result"]}
    return document
