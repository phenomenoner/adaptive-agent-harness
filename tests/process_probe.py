"""Black-box subprocess helper for reference-host restart conformance."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from aar.asset_models import AdaptiveAssetBundle, AdaptiveAssetDocument, AgentFingerprint
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.rlm_models import RlmJobSpec
from aar.runtime.continuity import (
    RLM_OPERATION_KIND,
    rlm_recovery_environment_digest,
    rlm_step_boundary_policy,
)
from aar.runtime.models import WorkspaceExecuteSpec
from aar.runtime.reference_host import ReferenceHost
from aar.schemas import (
    Budget,
    Grant,
    HostRef,
    LaneRef,
    OperationRef,
    PrincipalRef,
    RequestEnvelope,
    SessionRef,
    WorkspaceRef,
)

NOW_MS = 1_700_000_000_000


def _host(database: Path, *, durable: bool = False) -> ReferenceHost:
    return ReferenceHost(
        database,
        now_ms=lambda: NOW_MS,
        programmable_backend="plain",
        enable_durable_dispatch=durable,
        dispatcher_concurrency=1,
    )


def _accepted(host: ReferenceHost):
    principal = PrincipalRef(value="principal-process")
    session = SessionRef(value="session-process")
    handle = host.create_workspace(WorkspaceRef(value="workspace-process"), session)
    spec = WorkspaceExecuteSpec(
        workspace=handle.workspace,
        expected_generation=handle.generation,
        expected_revision=handle.revision,
        action="set",
        key="answer",
        value=42,
    )
    envelope = host.request_envelope(
        request_id="request-process",
        idempotency_key="idem-process-0001",
        principal=principal,
        session=session,
        workspace=handle,
        spec=spec,
        deadline_unix_ms=NOW_MS + 10_000,
    )
    return host.submit_execute(envelope, spec)


def _rlm_accepted(host: ReferenceHost, suffix: str):
    spec = RlmJobSpec(query=f"process recovery {suffix}", strategy="baseline", max_steps=1)
    envelope = host.request_rlm_envelope(
        request_id=f"request-rlm-{suffix}",
        idempotency_key=f"idempotency-rlm-{suffix}",
        principal=PrincipalRef(value="principal-process"),
        session=SessionRef(value="session-process"),
        spec=spec,
        deadline_unix_ms=NOW_MS + 10_000,
        budget=Budget(
            wall_time_ms=10_000,
            model_requests=1,
            input_tokens=1_024,
            output_tokens=64,
        ),
    )
    return host.submit_rlm(envelope, spec)


def _durable_claimed(host: ReferenceHost, suffix: str):
    accepted = _rlm_accepted(host, suffix)
    _bind_rlm_recovery_policy(host, accepted.operation)
    host.registry.request_dispatch(accepted.operation, kind="rlm.execute")
    owner = canonical_sha256({"process-owner": suffix})
    claim = host.registry.claim_next(
        host.runtime_generation,
        host.runtime_generation,
        owner,
        30_000,
    )
    assert claim is not None
    record = host.status(accepted.operation)
    envelope = RequestEnvelope.model_validate_json(record.request_json, strict=True)
    return accepted, envelope, claim, owner


def _bind_rlm_recovery_policy(host: ReferenceHost, operation: OperationRef) -> None:
    record = host.status(operation)
    spec = RlmJobSpec.model_validate_json(record.payload_json, strict=True)
    policy = rlm_step_boundary_policy()
    host.registry.bind_recovery_policy(
        operation,
        operation_kind=RLM_OPERATION_KIND,
        policy=policy,
        environment_digest=rlm_recovery_environment_digest(
            spec=spec,
            capability_digest=host.capabilities.digest,
            policy=policy,
        ),
    )


def _asset_accepted(host: ReferenceHost, suffix: str):
    document = AdaptiveAssetDocument.issue(
        AgentFingerprint(
            runtime_digest=canonical_sha256({"runtime": suffix}),
            capability_digest=host.capabilities.digest,
            policy_digest=canonical_sha256({"policy": "bounded"}),
        )
    )
    bundle = AdaptiveAssetBundle.issue(documents=(document,))
    principal = PrincipalRef(value="principal-process")
    deadline = NOW_MS + 10_000
    envelope = RequestEnvelope(
        request_id=f"request-asset-{suffix}",
        idempotency_key=f"idempotency-asset-{suffix}",
        host=HostRef(value="reference-host"),
        principal=principal,
        lane=LaneRef(value="asset-import"),
        session=SessionRef(value="session-process"),
        runtime_generation=host.runtime_generation,
        capability_digest=host.capabilities.digest,
        deadline_unix_ms=deadline,
        grants=(
            Grant(
                grant_id="grant-asset-import",
                capability="asset.import",
                issued_to=principal,
                expires_at_unix_ms=deadline,
            ),
        ),
        budget=Budget(wall_time_ms=10_000),
        trace_id=f"trace-asset-{suffix}",
        input_digest=canonical_sha256(bundle),
    )
    return host.submit_asset_import(envelope, bundle)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "commit-uncertain",
            "leave-running",
            "reconcile",
            "rlm-loss-before-broker",
            "rlm-loss-after-terminal",
            "rlm-cancel-loss-after-outer",
            "rlm-reconcile",
            "durable-claim-crash",
            "durable-receipt-crash",
            "durable-unresolved-crash",
            "durable-cancel-crash",
            "durable-recover",
            "asset-loss-after-import",
            "asset-reconcile",
        ),
    )
    parser.add_argument("database", type=Path)
    parser.add_argument("operation", nargs="?")
    args = parser.parse_args()

    host = _host(args.database, durable=args.action == "durable-recover")
    if args.action == "durable-recover":
        if args.operation is None:
            parser.error("durable-recover requires an operation id")
        operation = OperationRef(value=args.operation)
        record = host.wait_rlm(operation, timeout_s=2)
        snapshot = host.rlm_status(operation)
        continuity = host.registry.continuity_snapshot(operation)
        envelope = RequestEnvelope.model_validate_json(record.request_json, strict=True)
        page = host.registry.event_page(operation, limit=2)
        sequences = [event.sequence for event in page.events]
        event_kinds = [event.event_kind for event in page.events]
        while page.has_more:
            page = host.registry.event_page(
                operation,
                after_sequence=page.next_sequence,
                limit=2,
            )
            sequences.extend(event.sequence for event in page.events)
            event_kinds.extend(event.event_kind for event in page.events)
        print(
            json.dumps(
                {
                    "operation": operation.value,
                    "runtime_generation": host.runtime_generation,
                    "record": {
                        "state": record.state.value,
                        "certainty": record.certainty.value,
                        "reconciliation_required": record.reconciliation_required,
                    },
                    "snapshot": snapshot.model_dump(mode="json"),
                    "continuity": continuity.model_dump(mode="json"),
                    "decisions": [
                        decision.model_dump(mode="json")
                        for decision in host.registry.recovery_decisions(operation)
                    ],
                    "event_sequences": sequences,
                    "event_kinds": event_kinds,
                    "terminal_snapshot": (
                        None
                        if page.terminal_snapshot is None
                        else page.terminal_snapshot.model_dump(mode="json")
                    ),
                    "broker_trace_count": len(
                        host.brokers.bind(envelope, operation).traces()
                    ),
                },
                sort_keys=True,
            )
        )
        host.close()
        return 0
    if args.action == "rlm-reconcile":
        if args.operation is None:
            parser.error("rlm-reconcile requires an operation id")
        operation = OperationRef(value=args.operation)
        report = host.reconcile_rlm(operation)
        snapshot = host.rlm_status(operation)
        print(
            json.dumps(
                {
                    "report": report.model_dump(mode="json"),
                    "snapshot": snapshot.model_dump(mode="json"),
                },
                sort_keys=True,
            )
        )
        host.close()
        return 0
    if args.action == "asset-reconcile":
        if args.operation is None:
            parser.error("asset-reconcile requires an operation id")
        operation = OperationRef(value=args.operation)
        report = host.reconcile_asset_import(operation)
        record = host.status(operation)
        print(
            json.dumps(
                {
                    "report": report.model_dump(mode="json"),
                    "result": json.loads(record.result_json or "null"),
                    "counts": host.adaptive_assets.counts(),
                },
                sort_keys=True,
            )
        )
        host.close()
        return 0
    if args.action == "reconcile":
        if args.operation is None:
            parser.error("reconcile requires an operation id")
        report = host.reconcile(OperationRef(value=args.operation))
        print(canonical_json_bytes(report).decode())
        host.close()
        return 0

    if args.action in {"rlm-loss-before-broker", "rlm-loss-after-terminal"}:
        accepted = _rlm_accepted(host, args.action)
        print(accepted.operation.value, flush=True)
        failpoint = (
            "process_loss_before_first_broker"
            if args.action == "rlm-loss-before-broker"
            else "process_loss_after_terminal_receipt"
        )
        try:
            host.run_rlm(accepted.operation, failpoint=failpoint)
        except BaseException:
            os._exit(24 if args.action == "rlm-loss-before-broker" else 25)
        raise AssertionError("RLM process-loss failpoint did not fire")

    if args.action == "rlm-cancel-loss-after-outer":
        accepted = _rlm_accepted(host, args.action)
        print(accepted.operation.value, flush=True)
        try:
            host.cancel(accepted.operation, failpoint="process_loss_after_outer_cancel")
        except BaseException:
            os._exit(26)
        raise AssertionError("RLM cancellation process-loss failpoint did not fire")

    if args.action in {
        "durable-claim-crash",
        "durable-receipt-crash",
        "durable-unresolved-crash",
    }:
        accepted, envelope, claim, owner = _durable_claimed(host, args.action)
        print(
            json.dumps(
                {
                    "operation": accepted.operation.value,
                    "runtime_generation": host.runtime_generation,
                    "dispatcher_generation": claim.dispatcher_generation,
                    "attempt_no": claim.attempt.attempt_no,
                    "attempt_id": claim.attempt.attempt_id,
                    "lease_epoch": claim.lease_epoch,
                    "owner_digest": owner,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if args.action == "durable-claim-crash":
            os._exit(28)
        if args.action == "durable-unresolved-crash":

            def abrupt_loss(_prompt, _context):
                raise SystemExit(30)

            host.models.request = abrupt_loss
        try:
            host.rlm.run(
                accepted.operation,
                envelope,
                failpoint=(
                    "process_loss_after_broker_receipt"
                    if args.action == "durable-receipt-crash"
                    else None
                ),
            )
        except BaseException:
            os._exit(29 if args.action == "durable-receipt-crash" else 30)
        raise AssertionError("durable RLM process-loss failpoint did not fire")

    if args.action == "durable-cancel-crash":
        accepted = _rlm_accepted(host, args.action)
        _bind_rlm_recovery_policy(host, accepted.operation)
        host.registry.request_dispatch(accepted.operation, kind="rlm.execute")
        print(accepted.operation.value, flush=True)
        try:
            host.cancel(
                accepted.operation,
                failpoint="process_loss_after_outer_cancel",
                requested_by_digest=canonical_sha256({"actor": "process-probe"}),
                reason_code="process_probe",
            )
        except BaseException:
            os._exit(31)
        raise AssertionError("durable cancellation process-loss failpoint did not fire")

    if args.action == "asset-loss-after-import":
        accepted = _asset_accepted(host, args.action)
        print(accepted.operation.value, flush=True)
        try:
            host.run_asset_import(
                accepted.operation, failpoint="process_loss_after_atomic_import"
            )
        except BaseException:
            os._exit(27)
        raise AssertionError("asset process-loss failpoint did not fire")

    accepted = _accepted(host)
    if args.action == "commit-uncertain":
        record = host.run_execute(
            accepted.operation,
            failpoint="transport_loss_after_workspace_commit",
        )
        print(
            json.dumps(
                {
                    "operation": record.operation.value,
                    "runtime_generation": host.runtime_generation,
                },
                sort_keys=True,
            )
        )
        host.close()
        return 0


    host.registry.begin(accepted.operation, host.runtime_generation)
    print(accepted.operation.value, flush=True)
    os._exit(23)


if __name__ == "__main__":
    raise SystemExit(main())
