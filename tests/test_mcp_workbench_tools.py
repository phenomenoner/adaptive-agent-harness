from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from mcp import Client

from aar.broker_models import (
    EffectiveModelRoute,
    ModelResponse,
    ModelRouteBinding,
    ModelRouteReceipt,
    ModelUsageRecord,
)
from aar.canonical import canonical_sha256
from aar.mcp import workbench_surface
from aar.mcp.server import build_server
from aar.rlm_workbench_models import (
    CallerWorkTicket,
    RlmWorkbenchCapability,
    RlmWorkbenchFailure,
    RlmWorkbenchSnapshot,
)
from aar.runtime.caller_work import build_reconcile_fence
from aar.runtime.migrations import apply_registry_v6, create_sqlite_backup

ROOT = Path(__file__).resolve().parents[1]
SDD = ROOT / "docs/sdd/aar-rlm-native-workbench-v2"
NOW_MS = 1_700_000_000_000
METHODS = (
    "model.request",
    "subagent.submit",
    "subagent.result",
    "evidence.query",
    "artifact.put",
    "effect.propose",
)


def _prepare_v6_registry(database: Path, snapshot_path: Path) -> None:
    baseline_sql = SDD / "fixtures/registry-v5.sql"
    migration_sql = SDD / "migration-v6.sql"
    with sqlite3.connect(database) as connection:
        connection.executescript(baseline_sql.read_text(encoding="utf-8"))
    snapshot = create_sqlite_backup(database, snapshot_path)
    migration_bytes = migration_sql.read_bytes()
    payload: dict[str, object] = {
        "schema_version": "aar.migration-v6-attestation-payload.v1",
        "migration_version": 6,
        "cutover_epoch": "cutover-mcp-workbench-test",
        "snapshot_id": "snapshot-mcp-workbench-test",
        "snapshot_sha256": snapshot.sha256,
        "snapshot_size_bytes": snapshot.size_bytes,
        "canonical_v5_row_set_digest": "sha256:" + "2" * 64,
        "source_commit": "a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90",
        "wheel_digest": "sha256:" + "3" * 64,
        "profile_digest": "sha256:" + "4" * 64,
        "skill_digest": "sha256:" + "5" * 64,
        "contract_manifest_digest": "sha256:" + "6" * 64,
        "migration_sql_digest": "sha256:" + hashlib.sha256(migration_bytes).hexdigest(),
        "external_authority_store_id": "cutover-authority-mcp-workbench-test",
        "external_authority_prepared_digest": "sha256:" + "8" * 64,
        "started_at_unix_ms": NOW_MS,
        "completed_at_unix_ms": NOW_MS + 1,
        "foreign_key_violation_count": 0,
        "integrity_result": "ok",
    }
    apply_registry_v6(
        database,
        migration_sql_bytes=migration_bytes,
        attestation={
            "attestation": payload,
            "attestation_digest": canonical_sha256(payload),
        },
    )


def _structured(result: Any) -> dict[str, Any]:
    assert not result.is_error
    assert result.structured_content is not None
    return result.structured_content


def _read_context(capabilities: dict[str, Any]) -> dict[str, Any]:
    return {
        "runtime_generation": capabilities["ready"]["runtime_generation"],
        "capability_digest": capabilities["ready"]["capabilities"]["digest"],
        "principal_id": "principal-workbench-mcp",
        "session_id": "session-workbench-mcp",
        "deadline_unix_ms": NOW_MS + 900_000,
    }


def _execute_arguments(capabilities: dict[str, Any], *, suffix: str) -> dict[str, Any]:
    fixture = json.loads(
        (SDD / "fixtures/valid-workbench-execute.json").read_text(encoding="utf-8")
    )
    fixture["context"] = {
        **_read_context(capabilities),
        "schema_version": "aar.mcp-rlm-workbench-context.v1",
        "request_id": f"request-{suffix}",
        "idempotency_key": f"idempotency-{suffix}",
        "grant_ids": ["reference-grant-rlm-workbench-execute"],
        "budget_wall_time_ms": 900_000,
        "budget_model_requests": 40,
        "budget_input_tokens": 1_000_000,
        "budget_output_tokens": 500_000,
        "budget_child_operations": 8,
        "budget_artifact_bytes": 33_554_432,
    }
    fixture["start_only"] = True
    return fixture


def _configured_backends() -> dict[str, dict[str, Any]]:
    return {
        method: {
            "backend_kind": "caller_driver",
            "configured": True,
            "reference_only": False,
            "adapter_id": "mcp-caller-driver",
            "adapter_generation": 1,
            "evidence_tier": "caller_observed",
        }
        for method in METHODS
    }


def _pending_ticket(
    operation: dict[str, Any],
    *,
    suspension_revision: int,
    deadline_unix_ms: int,
    ticket_id: str,
) -> CallerWorkTicket:
    value = json.loads(
        (SDD / "fixtures/valid-ticket-cancelled-before-send-pending.json").read_text(
            encoding="utf-8"
        )
    )
    value.update(
        {
            "operation": operation,
            "suspension_revision": suspension_revision,
            "ticket_id": ticket_id,
            "revision": 0,
            "ticket_digest": "sha256:" + hashlib.sha256(ticket_id.encode()).hexdigest(),
            "state": "pending",
            "claimant": None,
            "physical_attempt": None,
            "settled_receipt_digest": None,
            "settled_at_unix_ms": None,
            "deadline_unix_ms": deadline_unix_ms,
        }
    )
    return CallerWorkTicket.model_validate(value, strict=True)


def _caller_common(
    legacy: dict[str, Any],
    authority: dict[str, Any],
    ticket: dict[str, Any],
    *,
    suffix: str,
) -> dict[str, Any]:
    return {
        "context": _execute_arguments(legacy, suffix=suffix)["context"],
        "operation": authority["operation"],
        "expected_control_revision": authority["control_revision"],
        "expected_cancellation_revision": authority["cancellation_revision"],
        "expected_suspension_revision": authority["suspension_revision"],
        "expected_cumulative_deadline_unix_ms": authority["cumulative_deadline_unix_ms"],
        "ticket_id": ticket["ticket_id"],
        "expected_revision": ticket["revision"],
        "ticket_digest": ticket["ticket_digest"],
        "idempotency_key": f"idempotency-{suffix}",
    }


def _model_commit_payload(ticket: dict[str, Any], output_text: str) -> dict[str, Any]:
    binding = ModelRouteBinding.model_validate(
        ticket["request"]["route_binding"], strict=True
    )
    route_receipt = ModelRouteReceipt.issue(
        requested=binding,
        effective=EffectiveModelRoute(
            provider_driver=binding.provider_driver,
            provider=binding.provider,
            model=binding.model,
            reasoning_effort=binding.reasoning_effort,
        ),
        finish_reason="stop",
        provider_response_id="provider-mcp-commit",
        lookup_supported=True,
    )
    usage = ModelUsageRecord(
        accounting_source="provider_reported",
        input_tokens=7,
        output_tokens=3,
        total_tokens=10,
    )
    response = ModelResponse(
        output_text=output_text,
        route_receipt=route_receipt,
        usage=usage,
    )
    return {
        "observation": {
            "kind": "model",
            "outcome": "succeeded",
            "output_text": output_text,
            "output_digest": "sha256:" + hashlib.sha256(output_text.encode()).hexdigest(),
            "route_receipt_digest": route_receipt.receipt_digest,
            "usage_receipt_digest": canonical_sha256(usage),
            "host_receipt_digest": canonical_sha256(response),
        },
        "model_response": response.model_dump(mode="json"),
    }


def test_installed_surface_loader_uses_the_combined_v8_asset(
    tmp_path: Path, monkeypatch: Any
) -> None:
    package_root = tmp_path / "aar"
    bundled = package_root / "bundled" / "schemas"
    bundled.mkdir(parents=True)
    source = ROOT / "schemas" / "aar-mcp-tools-v8-combined.json"
    (bundled / source.name).write_bytes(source.read_bytes())

    monkeypatch.setattr(workbench_surface, "_source_root", lambda: tmp_path / "empty")
    monkeypatch.setattr(
        workbench_surface.importlib.resources,
        "files",
        lambda package: package_root,
    )

    contract = workbench_surface.successor_surface_contract()
    assert contract["tool_surface_version"] == "aar.mcp-tools.v8"
    assert len(contract["tools"]) == 38
    assert tuple(tool["name"] for tool in contract["tools"][-8:]) == (
        workbench_surface.SUCCESSOR_TOOL_NAMES
    )


def test_workbench_capability_truth_and_unconfigured_admission_fail_closed(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "partial.sqlite3"
        _prepare_v6_registry(database, tmp_path / "partial.snapshot.sqlite3")
        application = build_server(
            database,
            now_ms=lambda: NOW_MS,
            enable_durable_dispatch=False,
        )
        try:
            async with Client(application.server) as client:
                legacy = _structured(await client.call_tool("aar_capabilities"))
                capability_payload = _structured(
                    await client.call_tool(
                        "aar_rlm_workbench_capabilities",
                        {"context": _read_context(legacy)},
                    )
                )
                capability = RlmWorkbenchCapability.model_validate(capability_payload, strict=True)
                rows = {row["method"]: row for row in capability.root["methods"]}
                assert rows["artifact.put"]["backend_kind"] == "native"
                assert rows["artifact.put"]["configured"] is True
                assert rows["model.request"]["backend_kind"] == "unconfigured"
                assert rows["model.request"]["configured"] is False

                failure_payload = _structured(
                    await client.call_tool(
                        "aar_rlm_workbench_execute",
                        _execute_arguments(legacy, suffix="partial"),
                    )
                )
                failure = RlmWorkbenchFailure.model_validate(failure_payload, strict=True)
                assert failure.root["code"] == "CAPABILITY_UNAVAILABLE"
        finally:
            application.close()

    asyncio.run(scenario())


def test_workbench_budget_authority_fails_before_operation_creation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "budget-authority.sqlite3"
        _prepare_v6_registry(database, tmp_path / "budget-authority.snapshot.sqlite3")
        application = build_server(
            database,
            now_ms=lambda: NOW_MS,
            enable_durable_dispatch=False,
            workbench_backend_availability=_configured_backends(),
        )
        try:
            async with Client(application.server) as client:
                legacy = _structured(await client.call_tool("aar_capabilities"))
                with sqlite3.connect(database) as connection:
                    baseline_operations = connection.execute(
                        "SELECT COUNT(*) FROM operations"
                    ).fetchone()[0]
                    baseline_jobs = connection.execute(
                        "SELECT COUNT(*) FROM rlm_workbench_jobs"
                    ).fetchone()[0]
                base = _execute_arguments(legacy, suffix="budget-base")
                spec_budget = base["spec"]["budgets"]
                route = base["spec"]["model"]["route_binding"]
                cases = {
                    "wall": ("budget_wall_time_ms", spec_budget["total_wall_time_ms"] - 1),
                    "model": ("budget_model_requests", spec_budget["max_model_calls"] - 1),
                    "output": ("budget_output_tokens", route["max_output_tokens"] - 1),
                    "child": ("budget_child_operations", spec_budget["max_subagent_calls"] - 1),
                    "artifact": ("budget_artifact_bytes", spec_budget["max_artifact_bytes"] - 1),
                    "input": ("budget_input_tokens", 0),
                }
                for suffix, (field, value) in cases.items():
                    arguments = _execute_arguments(legacy, suffix=f"budget-{suffix}")
                    arguments["context"][field] = value
                    failure_payload = _structured(
                        await client.call_tool("aar_rlm_workbench_execute", arguments)
                    )
                    failure = RlmWorkbenchFailure.model_validate(failure_payload, strict=True)
                    assert failure.root["code"] == "BUDGET_EXCEEDED"
                    with sqlite3.connect(database) as connection:
                        assert (
                            connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
                            == baseline_operations
                        ), suffix

                deadline = _execute_arguments(legacy, suffix="budget-deadline")
                deadline["context"]["deadline_unix_ms"] = (
                    NOW_MS + deadline["spec"]["budgets"]["total_wall_time_ms"] - 1
                )
                failure_payload = _structured(
                    await client.call_tool("aar_rlm_workbench_execute", deadline)
                )
                failure = RlmWorkbenchFailure.model_validate(failure_payload, strict=True)
                assert failure.root["code"] == "BUDGET_EXCEEDED"

            with sqlite3.connect(database) as connection:
                assert (
                    connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
                    == baseline_operations
                )
                assert (
                    connection.execute("SELECT COUNT(*) FROM rlm_workbench_jobs").fetchone()[0]
                    == baseline_jobs
                )
        finally:
            application.close()

    asyncio.run(scenario())


def test_workbench_execute_and_status_project_authoritative_snapshot(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "configured.sqlite3"
        _prepare_v6_registry(database, tmp_path / "configured.snapshot.sqlite3")
        application = build_server(
            database,
            now_ms=lambda: NOW_MS,
            enable_durable_dispatch=False,
            workbench_backend_availability=_configured_backends(),
        )
        try:
            async with Client(application.server) as client:
                legacy = _structured(await client.call_tool("aar_capabilities"))
                capability_payload = _structured(
                    await client.call_tool(
                        "aar_rlm_workbench_capabilities",
                        {"context": _read_context(legacy)},
                    )
                )
                capability = RlmWorkbenchCapability.model_validate(capability_payload, strict=True)
                assert all(
                    row["configured"] and not row["reference_only"]
                    for row in capability.root["methods"]
                )

                snapshot_payload = _structured(
                    await client.call_tool(
                        "aar_rlm_workbench_execute",
                        _execute_arguments(legacy, suffix="configured"),
                    )
                )
                snapshot = RlmWorkbenchSnapshot.model_validate(snapshot_payload, strict=True)
                assert snapshot.root["phase"] == "preparing_workspace"

                status_payload = _structured(
                    await client.call_tool(
                        "aar_rlm_workbench_status",
                        {
                            "context": _read_context(legacy),
                            "operation": snapshot.root["operation"],
                            "expected_revision": snapshot.root["revision"],
                        },
                    )
                )
                status = RlmWorkbenchSnapshot.model_validate(status_payload, strict=True)
                assert status.root == snapshot.root
        finally:
            application.close()

    asyncio.run(scenario())


def test_broker_caller_work_tools_forward_into_the_g4_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "caller-tools.sqlite3"
        _prepare_v6_registry(database, tmp_path / "caller-tools.snapshot.sqlite3")
        application = build_server(
            database,
            now_ms=lambda: NOW_MS,
            enable_durable_dispatch=False,
            workbench_backend_availability=_configured_backends(),
        )
        try:
            repository = application.host.caller_work
            assert repository is not None
            repository.bind_settlement_handler(None)
            async with Client(application.server) as client:
                legacy = _structured(await client.call_tool("aar_capabilities"))

                async def admit_operation(suffix: str) -> dict[str, Any]:
                    admitted = _structured(
                        await client.call_tool(
                            "aar_rlm_workbench_execute",
                            _execute_arguments(legacy, suffix=suffix),
                        )
                    )
                    return RlmWorkbenchSnapshot.model_validate(admitted, strict=True).root

                async def create_and_claim(
                    snapshot: dict[str, Any], ticket_id: str
                ) -> tuple[dict[str, Any], dict[str, Any]]:
                    suspension_revision = 1
                    with sqlite3.connect(database) as connection:
                        row = connection.execute(
                            """
                            SELECT control_revision, cancellation_revision,
                                   cumulative_deadline_unix_ms
                            FROM rlm_workbench_jobs WHERE operation_id = ?
                            """,
                            (snapshot["operation"]["value"],),
                        ).fetchone()
                        assert row is not None
                        authority = {
                            "operation": snapshot["operation"],
                            "control_revision": int(row[0]),
                            "cancellation_revision": int(row[1]),
                            "suspension_revision": suspension_revision,
                            "cumulative_deadline_unix_ms": int(row[2]),
                        }
                        ticket = _pending_ticket(
                            snapshot["operation"],
                            suspension_revision=suspension_revision,
                            deadline_unix_ms=int(row[2]),
                            ticket_id=ticket_id,
                        )
                        owner = ticket.root["owner"]
                        request = ticket.root["request"]
                        connection.execute(
                            "UPDATE rlm_workbench_jobs SET phase = 'waiting_external' "
                            "WHERE operation_id = ?",
                            (snapshot["operation"]["value"],),
                        )
                        connection.execute(
                            """
                            INSERT INTO rlm_workbench_suspensions(
                                operation_id, suspension_revision, control_revision,
                                logical_owner_json, logical_owner_digest, broker_method,
                                contract_id, request_digest, ticket_id, state,
                                created_at_unix_ms
                            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                            """,
                            (
                                snapshot["operation"]["value"],
                                suspension_revision,
                                int(row[0]),
                                json.dumps(owner, sort_keys=True, separators=(",", ":")),
                                canonical_sha256(owner),
                                request["method"],
                                request["contract_id"],
                                ticket.root["request_digest"],
                                ticket_id,
                                NOW_MS,
                            ),
                        )
                        connection.commit()
                    pending = repository.create_ticket(ticket).root
                    claim_arguments = {
                        **_caller_common(legacy, authority, pending, suffix=f"{ticket_id}-claim"),
                        "adapter_id": "mcp-caller-driver",
                        "adapter_generation": 1,
                        "claim_lease_ms": 5_000,
                    }
                    first = _structured(
                        await client.call_tool("aar_broker_work_claim", claim_arguments)
                    )
                    replay = _structured(
                        await client.call_tool("aar_broker_work_claim", claim_arguments)
                    )
                    assert replay == first
                    assert first["state"] == "send_reserved"
                    return first, authority

                cancel_snapshot = await admit_operation("caller-cancel")
                reserved_cancel, cancel_authority = await create_and_claim(
                    cancel_snapshot, "mcp-cancel-ticket"
                )
                claimant = reserved_cancel["claimant"]
                physical = reserved_cancel["physical_attempt"]
                cancelled = _structured(
                    await client.call_tool(
                        "aar_broker_work_cancel_before_send",
                        {
                            **_caller_common(
                                legacy,
                                cancel_authority,
                                reserved_cancel,
                                suffix="cancel-before-send",
                            ),
                            "expected_pre_send_state": "send_reserved",
                            "claim_id": claimant["claim_id"],
                            "claim_fence": claimant["claim_fence"],
                            "physical_attempt_id": physical["physical_attempt_id"],
                            "expected_claim_expires_at_unix_ms": claimant[
                                "claim_expires_at_unix_ms"
                            ],
                            "settled_receipt_digest": "sha256:" + "a" * 64,
                            "settled_at_unix_ms": NOW_MS + 1,
                            "reason": "user_requested",
                        },
                    )
                )
                assert cancelled.get("state") == "cancelled_before_send", cancelled

                commit_snapshot = await admit_operation("caller-commit")
                reserved_commit, commit_authority = await create_and_claim(
                    commit_snapshot, "mcp-commit-ticket"
                )
                claimant = reserved_commit["claimant"]
                physical = reserved_commit["physical_attempt"]
                started = _structured(
                    await client.call_tool(
                        "aar_broker_work_mark_send_started",
                        {
                            **_caller_common(
                                legacy,
                                commit_authority,
                                reserved_commit,
                                suffix="mark-send-started",
                            ),
                            "claim_id": claimant["claim_id"],
                            "claim_fence": claimant["claim_fence"],
                            "physical_attempt_id": physical["physical_attempt_id"],
                            "expected_claim_expires_at_unix_ms": claimant[
                                "claim_expires_at_unix_ms"
                            ],
                            "provider_or_child_idempotency_key": physical[
                                "provider_or_child_idempotency_key"
                            ],
                            "sent_request_digest": reserved_commit["request_digest"],
                            "lookup_supported": True,
                            "cancel_supported": True,
                        },
                    )
                )
                assert started["state"] == "send_started"
                commit_arguments = {
                    **_caller_common(legacy, commit_authority, started, suffix="commit"),
                    "claim_id": claimant["claim_id"],
                    "claim_fence": claimant["claim_fence"],
                    "physical_attempt_id": physical["physical_attempt_id"],
                    "sent_request_digest": started["request_digest"],
                    "sent_at_unix_ms": NOW_MS + 2,
                    "provider_or_child_request_id": "provider-request-commit",
                    **_model_commit_payload(started, "ok"),
                }
                committed = _structured(
                    await client.call_tool("aar_broker_work_commit", commit_arguments)
                )
                assert committed.get("state") == "settled_success", committed
                replayed = _structured(
                    await client.call_tool("aar_broker_work_commit", commit_arguments)
                )
                assert replayed == committed
                changed_arguments = {
                    **commit_arguments,
                    **_model_commit_payload(started, "changed"),
                }
                collision_payload = _structured(
                    await client.call_tool("aar_broker_work_commit", changed_arguments)
                )
                collision = RlmWorkbenchFailure.model_validate(
                    collision_payload, strict=True
                )
                assert collision.root["code"] == "CALLER_WORK_CONFLICT"
                with sqlite3.connect(database) as connection:
                    connection.row_factory = sqlite3.Row
                    durable = connection.execute(
                        """
                        SELECT ticket.state,
                               (SELECT COUNT(*) FROM caller_work_candidate_receipts
                                WHERE ticket_id = ticket.ticket_id) AS candidate_count,
                               (SELECT COUNT(*) FROM model_executions
                                WHERE operation_id = ticket.operation_id
                                  AND idempotency_key = ticket.external_idempotency_key
                                  AND state = 'result_committed') AS journal_count,
                               (SELECT COUNT(*) FROM caller_work_command_receipts
                                WHERE operation_id = ticket.operation_id
                                  AND command_kind = 'commit') AS command_count
                        FROM caller_work_tickets AS ticket WHERE ticket.ticket_id = ?
                        """,
                        (started["ticket_id"],),
                    ).fetchone()
                assert durable is not None
                assert tuple(durable) == ("settled_success", 1, 1, 1)

                reconcile_snapshot = await admit_operation("caller-reconcile")
                reserved_reconcile, reconcile_authority = await create_and_claim(
                    reconcile_snapshot, "mcp-reconcile-ticket"
                )
                claimant = reserved_reconcile["claimant"]
                physical = reserved_reconcile["physical_attempt"]
                started_unknown = _structured(
                    await client.call_tool(
                        "aar_broker_work_mark_send_started",
                        {
                            **_caller_common(
                                legacy,
                                reconcile_authority,
                                reserved_reconcile,
                                suffix="mark-unknown",
                            ),
                            "claim_id": claimant["claim_id"],
                            "claim_fence": claimant["claim_fence"],
                            "physical_attempt_id": physical["physical_attempt_id"],
                            "expected_claim_expires_at_unix_ms": claimant[
                                "claim_expires_at_unix_ms"
                            ],
                            "provider_or_child_idempotency_key": physical[
                                "provider_or_child_idempotency_key"
                            ],
                            "sent_request_digest": reserved_reconcile["request_digest"],
                            "lookup_supported": True,
                            "cancel_supported": True,
                        },
                    )
                )
                reconcile_fence = build_reconcile_fence(
                    ticket_id=started_unknown["ticket_id"],
                    expected_revision=started_unknown["revision"],
                    physical_attempt_id=physical["physical_attempt_id"],
                    candidate_receipt_digest=None,
                    reconciler_id="mcp-caller-driver",
                    reconciler_generation=1,
                    reconciliation_action="lookup",
                )
                reconciled = _structured(
                    await client.call_tool(
                        "aar_broker_work_reconcile",
                        {
                            **_caller_common(
                                legacy,
                                reconcile_authority,
                                started_unknown,
                                suffix="reconcile",
                            ),
                            "physical_attempt_id": physical["physical_attempt_id"],
                            "candidate_receipt_digest": None,
                            "reconciler_id": "mcp-caller-driver",
                            "reconciler_generation": 1,
                            "reconcile_fence": reconcile_fence,
                            "reconciliation_action": "lookup",
                        },
                    )
                )
                assert reconciled.get("state") == "send_started", reconciled
        finally:
            application.close()

    asyncio.run(scenario())
