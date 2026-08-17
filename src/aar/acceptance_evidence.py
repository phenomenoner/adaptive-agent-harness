"""Fail-closed preflight for evidence-backed AR-RW acceptance claims."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class AcceptanceClaimError(ValueError):
    """A release claim lacks required row, fault, or evidence coverage."""


def validate_acceptance_claim(
    *,
    acceptance_matrix: Mapping[str, Any],
    fault_matrix: Mapping[str, Any],
    claim: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Reject verified/live claims that cite matrices without result evidence."""

    claim_name = claim.get("claim", claim.get("claim_status"))
    if claim_name not in {"implementation_verified", "live_qualified"}:
        return claim
    required_key = (
        "required_for_live_qualified"
        if claim_name == "live_qualified"
        else "required_for_implementation_verified"
    )
    rows = _objects(acceptance_matrix.get("rows"), "acceptance rows")
    cases = _objects(fault_matrix.get("cases"), "fault cases")
    required_rows = {
        _identifier(row, "id", "acceptance row")
        for row in rows
        if row.get(required_key) is True
    }
    required_faults = {
        _identifier(case, "id", "fault case")
        for case in cases
        if case.get("required") is True
        and case.get("acceptance_id") in required_rows
    }
    if not required_rows:
        raise AcceptanceClaimError("verified claim has no required acceptance row set")

    row_results = _objects(claim.get("row_results"), "row results")
    fault_results = _objects(claim.get("fault_results"), "fault results")
    evidence = _objects(claim.get("evidence"), "evidence")
    passing_rows = {
        _result_identifier(item, ("acceptance_id", "row_id", "id"))
        for item in row_results
        if _is_pass(item)
    }
    passing_faults = {
        _result_identifier(item, ("fault_case_id", "fault_id", "id"))
        for item in fault_results
        if _is_pass(item)
    }
    evidence_rows = {
        str(item["acceptance_id"])
        for item in evidence
        if item.get("acceptance_id") is not None
    }
    evidence_faults = {
        str(item["fault_case_id"])
        for item in evidence
        if item.get("fault_case_id") is not None
    }

    missing_rows = sorted(required_rows - passing_rows)
    missing_faults = sorted(required_faults - passing_faults)
    missing_row_evidence = sorted(required_rows - evidence_rows)
    missing_fault_evidence = sorted(required_faults - evidence_faults)
    if missing_rows or missing_faults or missing_row_evidence or missing_fault_evidence:
        raise AcceptanceClaimError(
            "implementation claim lacks required row/fault/evidence coverage: "
            f"rows={missing_rows}; faults={missing_faults}; "
            f"row_evidence={missing_row_evidence}; "
            f"fault_evidence={missing_fault_evidence}"
        )
    return claim


def _objects(value: Any, owner: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise AcceptanceClaimError(f"{owner} must be an array of objects")
    if not all(isinstance(item, Mapping) for item in value):
        raise AcceptanceClaimError(f"{owner} must contain only objects")
    return tuple(value)


def _identifier(item: Mapping[str, Any], key: str, owner: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise AcceptanceClaimError(f"{owner} has no stable id")
    return value


def _result_identifier(
    item: Mapping[str, Any],
    keys: tuple[str, ...],
) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _is_pass(item: Mapping[str, Any]) -> bool:
    value = item.get("status", item.get("outcome", item.get("result")))
    return value in {"pass", "passed", "succeeded"}
