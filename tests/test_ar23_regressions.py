from __future__ import annotations

import asyncio
import copy
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from aar.asset_models import (
    AdaptiveAssetBundle,
    AdaptiveAssetDocument,
    AgentFingerprint,
    Episode,
    MaterializationRequest,
    Proposal,
)
from aar.benchmark import (
    BenchmarkCaseResult,
    BenchmarkMetrics,
    BenchmarkObservation,
    BenchmarkReport,
    BenchmarkSummary,
    load_pack,
    run_pack,
)
from aar.broker_models import BrokerUsage
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.mcp.server import build_server
from aar.schemas import OperationRef, OutcomeCertainty

NOW_MS = 1_700_000_000_000
ROOT = Path(__file__).resolve().parents[1]


def _structured(result) -> dict[str, Any]:
    assert not result.is_error
    assert result.structured_content is not None
    return result.structured_content


async def _capabilities(client: Client) -> dict[str, Any]:
    return _structured(await client.call_tool("aar_capabilities"))


def _read_context(capabilities: dict[str, Any], *, session: str = "session-a"):
    return {
        "runtime_generation": capabilities["ready"]["runtime_generation"],
        "capability_digest": capabilities["ready"]["capabilities"]["digest"],
        "principal_id": "principal-shared",
        "session_id": session,
        "deadline_unix_ms": NOW_MS + 10_000,
    }


def _mutation_context(
    capabilities: dict[str, Any],
    suffix: str,
    capability: str,
    *,
    session: str = "session-a",
):
    grants = {
        descriptor["capability"]: descriptor["grant_id"]
        for descriptor in capabilities["reference_grants"]
    }
    return {
        **_read_context(capabilities, session=session),
        "request_id": f"request-{suffix}",
        "idempotency_key": f"idempotency-{suffix}",
        "grant_id": grants[capability],
        "budget_wall_time_ms": 10_000,
    }


def _rlm_context(
    capabilities: dict[str, Any], suffix: str, *, session: str = "session-a"
):
    grants = {
        descriptor["capability"]: descriptor["grant_id"]
        for descriptor in capabilities["reference_grants"]
    }
    return {
        **_read_context(capabilities, session=session),
        "request_id": f"request-{suffix}",
        "idempotency_key": f"idempotency-{suffix}",
        "grant_ids": sorted((grants["model.request"], grants["rlm.execute"])),
        "budget_wall_time_ms": 10_000,
        "budget_model_requests": 1,
        "budget_input_tokens": 1_024,
        "budget_output_tokens": 64,
        "budget_child_operations": 0,
        "budget_artifact_bytes": 0,
    }


async def _call(client: Client, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _structured(await client.call_tool(tool, arguments))


async def _program_create(
    client: Client, capabilities: dict[str, Any], suffix: str
) -> dict[str, Any]:
    result = await _call(
        client,
        "aar_program_workspace_create",
        {
            "context": _mutation_context(
                capabilities, f"setup-create-{suffix}", "workspace.program.create"
            ),
            "workspace_id": f"workspace-program-{suffix}",
        },
    )
    assert result["failure"] is None
    return result["handle"]


async def _program_execute(
    client: Client,
    capabilities: dict[str, Any],
    suffix: str,
    handle: dict[str, Any],
) -> dict[str, Any]:
    result = await _call(
        client,
        "aar_program_workspace_execute",
        {
            "context": _mutation_context(
                capabilities, f"setup-execute-{suffix}", "workspace.program.execute"
            ),
            "handle": handle,
            "code": "answer = 42\nanswer",
            "wall_time_ms": 1_000,
        },
    )
    assert result["failure"] is None
    return {
        "workspace": result["result"]["workspace"],
        "backend": result["result"]["backend"],
        "generation": result["result"]["generation"],
        "revision": result["result"]["revision_after"],
    }


def _asset_bundle(capabilities: dict[str, Any], suffix: str) -> AdaptiveAssetBundle:
    fingerprint = AdaptiveAssetDocument.issue(
        AgentFingerprint(
            runtime_digest=canonical_sha256({"runtime": suffix}),
            capability_digest=capabilities["ready"]["capabilities"]["digest"],
            policy_digest=canonical_sha256({"policy": "bounded"}),
        )
    )
    episode = AdaptiveAssetDocument.issue(
        Episode(
            operation=OperationRef(value=f"operation-{suffix}"),
            agent=fingerprint.manifest.asset,
            request_digest=canonical_sha256({"request": suffix}),
        )
    )
    return AdaptiveAssetBundle.issue(documents=(fingerprint, episode))


async def _target_call(
    family: str, client: Client, capabilities: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    if family == "workspace-create":
        return (
            "aar_workspace_create",
            {
                "context": _mutation_context(
                    capabilities, "target-workspace-create", "workspace.create"
                ),
                "workspace_id": "workspace-target-scalar",
            },
        )
    if family == "workspace-execute":
        created = await _call(
            client,
            "aar_workspace_create",
            {
                "context": _mutation_context(
                    capabilities, "setup-workspace-create", "workspace.create"
                ),
                "workspace_id": "workspace-target-execute",
            },
        )
        return (
            "aar_workspace_execute",
            {
                "context": _mutation_context(
                    capabilities, "target-workspace-execute", "workspace.execute"
                ),
                "workspace_id": "workspace-target-execute",
                "expected_generation": created["handle"]["generation"],
                "expected_revision": created["handle"]["revision"],
                "action": "set",
                "key": "answer",
                "value": 42,
            },
        )
    if family == "program-create":
        return (
            "aar_program_workspace_create",
            {
                "context": _mutation_context(
                    capabilities, "target-program-create", "workspace.program.create"
                ),
                "workspace_id": "workspace-target-program-create",
            },
        )
    if family == "program-execute":
        handle = await _program_create(client, capabilities, "target-execute")
        return (
            "aar_program_workspace_execute",
            {
                "context": _mutation_context(
                    capabilities, "target-program-execute", "workspace.program.execute"
                ),
                "handle": handle,
                "code": "value = 7\nvalue",
                "wall_time_ms": 1_000,
            },
        )
    if family == "program-checkpoint":
        created = await _program_create(client, capabilities, "target-checkpoint")
        handle = await _program_execute(
            client, capabilities, "target-checkpoint", created
        )
        return (
            "aar_program_workspace_checkpoint",
            {
                "context": _mutation_context(
                    capabilities,
                    "target-program-checkpoint",
                    "workspace.program.checkpoint",
                ),
                "handle": handle,
            },
        )
    if family == "program-restore":
        created = await _program_create(client, capabilities, "target-restore")
        handle = await _program_execute(client, capabilities, "target-restore", created)
        checkpoint = await _call(
            client,
            "aar_program_workspace_checkpoint",
            {
                "context": _mutation_context(
                    capabilities,
                    "setup-program-checkpoint-restore",
                    "workspace.program.checkpoint",
                ),
                "handle": handle,
            },
        )
        return (
            "aar_program_workspace_restore",
            {
                "context": _mutation_context(
                    capabilities, "target-program-restore", "workspace.program.restore"
                ),
                "workspace_id": "workspace-program-target-restore",
                "manifest": checkpoint["manifest"],
                "expected_handle": handle,
            },
        )
    if family == "program-close":
        handle = await _program_create(client, capabilities, "target-close")
        return (
            "aar_program_workspace_close",
            {
                "context": _mutation_context(
                    capabilities, "target-program-close", "workspace.program.close"
                ),
                "handle": handle,
                "reason": "cross-session regression",
            },
        )
    if family == "rlm-execute":
        return (
            "aar_rlm_execute",
            {
                "context": _rlm_context(capabilities, "target-rlm-execute"),
                "query": "portable runtime",
                "strategy": "baseline",
                "max_steps": 1,
            },
        )
    if family == "asset-import":
        bundle = _asset_bundle(capabilities, "target-asset-import")
        return (
            "aar_asset_import",
            {
                "context": _mutation_context(
                    capabilities, "target-asset-import", "asset.import"
                ),
                "bundle": bundle.model_dump(mode="json"),
            },
        )
    raise AssertionError(family)


@pytest.mark.parametrize(
    "family",
    (
        "workspace-create",
        "workspace-execute",
        "program-create",
        "program-execute",
        "program-checkpoint",
        "program-restore",
        "program-close",
        "rlm-execute",
        "asset-import",
    ),
)
def test_public_idempotent_replay_is_session_bound(
    tmp_path: Path, family: str
) -> None:
    async def scenario() -> None:
        application = build_server(
            tmp_path / f"replay-{family}.sqlite3",
            now_ms=lambda: NOW_MS,
            programmable_backend="plain",
        )
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                tool, arguments = await _target_call(family, client, capabilities)
                first = await _call(client, tool, arguments)
                assert first.get("failure") is None

                foreign = copy.deepcopy(arguments)
                foreign["context"]["session_id"] = "session-b"
                foreign["context"]["request_id"] += "-foreign"
                replay = await _call(client, tool, foreign)

                assert replay["failure"]["code"] in {
                    "AUTHORITY_DENIED",
                    "OPERATION_CONFLICT",
                }
                assert replay.get("handle") is None
                assert replay.get("result") is None
                assert replay.get("manifest") is None
        finally:
            application.close()

    asyncio.run(scenario())


def _database_snapshot(database: Path) -> tuple[tuple[str, tuple[tuple[Any, ...], ...]], ...]:
    connection = sqlite3.connect(database)
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        snapshot: list[tuple[str, tuple[tuple[Any, ...], ...]]] = []
        for table in tables:
            rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            normalized = tuple(
                tuple(value.hex() if isinstance(value, bytes) else value for value in row)
                for row in rows
            )
            snapshot.append((table, normalized))
        return tuple(snapshot)
    finally:
        connection.close()


@pytest.mark.parametrize("status_tool", ("aar_operation_status", "aar_rlm_status"))
def test_read_only_status_is_zero_write(tmp_path: Path, status_tool: str) -> None:
    async def scenario() -> None:
        database = tmp_path / f"status-{status_tool}.sqlite3"
        application = build_server(
            database, now_ms=lambda: NOW_MS, programmable_backend="plain"
        )
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                accepted = await _call(
                    client,
                    "aar_rlm_execute",
                    {
                        "context": _rlm_context(capabilities, f"status-{status_tool}"),
                        "query": "status without writes",
                        "strategy": "baseline",
                        "max_steps": 1,
                        "start_only": True,
                    },
                )
                operation = OperationRef(value=accepted["operation"]["value"])
                terminal = application.host.wait_rlm(operation, timeout_s=2)
                assert terminal.state.value == "succeeded"

                before = _database_snapshot(database)
                observed = await _call(
                    client,
                    status_tool,
                    {
                        "context": _read_context(capabilities),
                        "operation_id": operation.value,
                    },
                )
                assert observed["failure"] is None
                after = _database_snapshot(database)
                assert after == before
        finally:
            application.close()

    asyncio.run(scenario())


def test_missing_rlm_state_is_reported_without_repairing_on_read(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "missing-rlm-state.sqlite3"
        application = build_server(
            database, now_ms=lambda: NOW_MS, programmable_backend="plain"
        )
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                accepted = await _call(
                    client,
                    "aar_rlm_execute",
                    {
                        "context": _rlm_context(capabilities, "missing-rlm-state"),
                        "query": "missing state",
                        "strategy": "baseline",
                        "max_steps": 1,
                        "start_only": True,
                    },
                )
                operation_id = accepted["operation"]["value"]
                terminal = application.host.wait_rlm(
                    OperationRef(value=operation_id), timeout_s=2
                )
                assert terminal.state.value == "succeeded"
                with sqlite3.connect(database) as connection:
                    connection.execute(
                        "DELETE FROM rlm_jobs WHERE operation_id = ?", (operation_id,)
                    )

                before = _database_snapshot(database)
                observed = await _call(
                    client,
                    "aar_rlm_status",
                    {
                        "context": _read_context(capabilities),
                        "operation_id": operation_id,
                    },
                )
                assert observed["failure"]["code"] == "RLM_STATE_MISSING"
                assert observed["snapshot"] is None
                assert _database_snapshot(database) == before
        finally:
            application.close()

    asyncio.run(scenario())


def _benchmark_report(*, baseline_elapsed: int, rlm_elapsed: int) -> BenchmarkReport:
    common = {
        "evidence_expected": 1,
        "latency_budget_ms": 10_000,
        "usage": BrokerUsage(),
        "certainty": OutcomeCertainty.CERTAIN,
        "reconciliation_required": False,
        "provider_credentials_available": False,
        "effect_execution_available": False,
        "final_delivery_available": False,
    }
    baseline = BenchmarkObservation(
        mode="broker_baseline",
        answer_digest=canonical_sha256({"answer": "baseline"}),
        metrics=BenchmarkMetrics(
            evidence_hits=0,
            observed_elapsed_ms=baseline_elapsed,
            broker_calls=1,
            **common,
        ),
    )
    rlm = BenchmarkObservation(
        mode="evidence_rlm",
        answer_digest=canonical_sha256({"answer": "rlm"}),
        metrics=BenchmarkMetrics(
            evidence_hits=1,
            observed_elapsed_ms=rlm_elapsed,
            broker_calls=2,
            **common,
        ),
    )
    case = BenchmarkCaseResult(
        case_id="deterministic",
        baseline=baseline,
        rlm=rlm,
        evidence_hit_delta=1,
        broker_call_delta=1,
        input_token_delta=0,
    )
    summary = BenchmarkSummary(
        case_count=1,
        baseline_evidence_hits=0,
        rlm_evidence_hits=1,
        evidence_hit_delta=1,
        broker_call_delta=1,
        input_token_delta=0,
        all_safety_boundaries_preserved=True,
        interpretation="Observed timing is noncanonical telemetry.",
    )
    return BenchmarkReport.issue(pack_name="deterministic-pack", cases=(case,), summary=summary)


def test_benchmark_digest_excludes_observational_timing_only() -> None:
    fast = _benchmark_report(baseline_elapsed=1, rlm_elapsed=2)
    slow = _benchmark_report(baseline_elapsed=101, rlm_elapsed=202)
    assert fast.cases != slow.cases
    assert fast.report_digest == slow.report_digest
    BenchmarkReport.model_validate(slow.model_copy(update={"report_digest": fast.report_digest}))


def test_repeated_real_benchmark_runs_have_one_semantic_digest(tmp_path: Path) -> None:
    pack = load_pack(ROOT / "benchmarks" / "rlm-evidence-v1.json")
    first = run_pack(pack, tmp_path / "first")
    second = run_pack(pack, tmp_path / "second")
    assert first.report_digest == second.report_digest


def test_concrete_materializer_prepare_is_deterministic_and_effect_free() -> None:
    from aar.assets import DeterministicPrepareOnlyMaterializer

    source = AdaptiveAssetDocument.issue(
        AgentFingerprint(
            runtime_digest=canonical_sha256({"runtime": "materializer"}),
            capability_digest=canonical_sha256({"capabilities": "bounded"}),
            policy_digest=canonical_sha256({"policy": "proposal-only"}),
        )
    )
    proposal = Proposal(
        target="skills/example",
        based_on=(source.manifest.asset,),
        candidate_digest=canonical_sha256({"candidate": "v2"}),
        rollback_digest=canonical_sha256({"candidate": "v1"}),
        rationale_digest=canonical_sha256({"rationale": "verified evidence"}),
        confidence_milli=800,
    )
    proposal_document = AdaptiveAssetDocument.issue(proposal)
    request = MaterializationRequest(
        proposal=proposal_document.manifest.asset,
        expected_serving_digest=canonical_sha256({"serving": "v1"}),
        idempotency_key="materialize-example-0001",
    )
    materializer = DeterministicPrepareOnlyMaterializer()

    first = materializer.prepare(request, proposal)
    second = materializer.prepare(request, proposal)

    assert first == second
    assert first.candidate_digest == proposal.candidate_digest
    assert first.rollback_digest == proposal.rollback_digest
    assert first.activation_required is True
    assert first.external_effect_performed is False
    assert materializer.describe().activation_available is False


def test_mcp_rejects_dependency_incomplete_asset_bundle_without_catalog_mutation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "asset-missing-dependency.sqlite3"
        application = build_server(
            database, now_ms=lambda: NOW_MS, programmable_backend="plain"
        )
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                complete = _asset_bundle(capabilities, "missing-dependency")
                episode = next(
                    document
                    for document in complete.documents
                    if document.manifest.asset.kind == "episode"
                )
                core = {
                    "schema_version": "aar.asset-bundle.v1",
                    "documents": (episode,),
                    "events": (),
                }
                invalid = {
                    "schema_version": "aar.asset-bundle.v1",
                    "documents": [episode.model_dump(mode="json")],
                    "events": [],
                    "bundle_digest": canonical_sha256(core),
                }
                before = application.host.adaptive_assets.counts()
                observed = await _call(
                    client,
                    "aar_asset_import",
                    {
                        "context": _mutation_context(
                            capabilities,
                            "invalid-missing-dependency",
                            "asset.import",
                        ),
                        "bundle": invalid,
                    },
                )
                assert observed["failure"] is not None
                assert application.host.adaptive_assets.counts() == before
        finally:
            application.close()

    asyncio.run(scenario())


def test_mcp_rejects_existing_digest_under_conflicting_kind_without_partial_import(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "asset-conflicting-kind.sqlite3"
        application = build_server(
            database, now_ms=lambda: NOW_MS, programmable_backend="plain"
        )
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                bundle = _asset_bundle(capabilities, "conflicting-kind")
                document = bundle.documents[0]
                with sqlite3.connect(database) as connection:
                    connection.execute(
                        "INSERT INTO asset_bodies(body_digest, kind, schema_version, body_json) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            document.manifest.body_digest,
                            document.manifest.asset.kind,
                            document.manifest.body_schema_version,
                            canonical_json_bytes(document.body).decode(),
                        ),
                    )
                    connection.execute(
                        "INSERT INTO asset_manifests(manifest_digest, kind, body_digest, "
                        "document_json) VALUES (?, ?, ?, ?)",
                        (
                            document.manifest.manifest_digest,
                            "proposal",
                            document.manifest.body_digest,
                            canonical_json_bytes(document).decode(),
                        ),
                    )
                before = application.host.adaptive_assets.counts()
                observed = await _call(
                    client,
                    "aar_asset_import",
                    {
                        "context": _mutation_context(
                            capabilities, "invalid-conflicting-kind", "asset.import"
                        ),
                        "bundle": bundle.model_dump(mode="json"),
                    },
                )
                assert observed["failure"]["code"] == "ASSET_CONFLICT"
                assert application.host.adaptive_assets.counts() == before
        finally:
            application.close()

    asyncio.run(scenario())
