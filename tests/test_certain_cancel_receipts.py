"""Real typed caller receipts; no child agent or provider is executed."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
import test_caller_work_lifecycle as support

from aar.canonical import canonical_sha256
from aar.rlm_workbench_models import CallerWorkCommitInput

if TYPE_CHECKING:
    from collections.abc import Mapping

    from aar.rlm_workbench_models import RlmWorkbenchJobSpec, RlmWorkbenchSnapshot
    from aar.schemas import OperationRef


def test_schema_valid_cancelled_child_receipt_settles_as_certain_cancel(tmp_path: Path) -> None:
    ticket = support.pending_ticket()
    result_contract = ticket["request"]["response_contract"]
    ticket["request"] = {
        "contract_id": "aar.broker-contract.subagent-submit.v2",
        "method": "subagent.submit",
        "task": "controlled child; no child is launched",
        "child_profile_id": "controlled-child-profile",
        "result_contract": result_contract,
        "max_depth": 1,
        "required": True,
    }
    ticket["request_digest"] = canonical_sha256(ticket["request"])
    with patch.object(support, "pending_ticket", return_value=ticket):
        repo = support.new_repository(tmp_path, support.MutableClock(support.FIXED_NOW_MS))
    try:
        pending = repo.get(support.TICKET_ID)
        claimed = repo.claim(support.claim_input(pending))
        started = repo.mark_send_started(
            support.mark_send_started_input(claimed, idempotency_key="cancel-send")
        )
        command = {
            **support.common_input(started, idempotency_key="cancel-commit"),
            "claim_id": started.claimant.claim_id,
            "claim_fence": started.claimant.claim_fence,
            "physical_attempt_id": started.physical_attempt.physical_attempt_id,
            "sent_request_digest": started.request_digest,
            "sent_at_unix_ms": support.FIXED_NOW_MS + 2,
            "provider_or_child_request_id": "controlled-child-cancel",
            "model_response": None,
            "observation": {
                "kind": "subagent",
                "outcome": "cancelled_certain",
                "child_execution_id": "controlled-child-execution",
                "result_digest": None,
                "artifact_manifest_digest": None,
                "host_receipt_digest": canonical_sha256("controlled cancellation receipt"),
            },
        }
        # Reach the real commit after the CURRENT schema accepts the spelling.
        CallerWorkCommitInput.model_validate(command, strict=True)
        settled = repo.commit(command)
        assert settled.state == "cancelled_certain"
        assert settled.settled_receipt_digest is not None
        assert repo.send_started_count(started.ticket_id) == 1
        replay = repo.commit(command)
        assert replay.root == settled.root
        assert repo.send_started_count(started.ticket_id) == 1
    finally:
        repo.close()


@pytest.mark.parametrize("outcome", ["cancelled_certain", "outcome_unknown"])
def test_certain_cancel_receipt_resumes_real_cell_then_native_abstention(
    tmp_path: Path, outcome: str
) -> None:
    import json
    import sqlite3

    from test_rlm_workbench import (
        FIXED_NOW_MS,
        caller_common_input,
        prepare_v6_registry,
        wait_for_pending_ticket,
        workbench_envelope,
        workbench_request,
    )

    from aar.runtime.reference_host import ReferenceHost

    request_contract = support.pending_ticket()["request"]["response_contract"]
    subagent = {
        "contract_id": "aar.broker-contract.subagent-submit.v2",
        "method": "subagent.submit",
        "task": "controlled; no child is launched",
        "child_profile_id": "controlled-child-profile",
        "result_contract": request_contract,
        "max_depth": 1,
        "required": True,
    }

    class Planner:
        calls = 0

        def plan(
            self, operation: OperationRef, spec: RlmWorkbenchJobSpec, snapshot: RlmWorkbenchSnapshot
        ) -> Mapping[str, Any]:
            self.calls += 1
            if self.calls == 1:
                return {
                    "kind": "execute_cell",
                    "code": (
                        f"import json\nr = aar_broker(json.loads({json.dumps(subagent)!r}))\n"
                        "assert r['ticket_state'] == 'cancelled_certain'\n"
                        "assert r['observation']['outcome'] == 'cancelled_certain'\n"
                        "aar_artifact('cancel-proof.txt', "
                        "'known cancellation receipt reached the real cell', 'text/plain')\n"
                    ),
                    "expected_result_hint": "controlled cancellation",
                }
            return {"kind": "abstain", "reason": "controlled post-cancellation abstention"}

    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "v5.sqlite")
    planner = Planner()
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=planner,
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
        workbench_backend_availability={
            "subagent.submit": {
                "configured": True,
                "reference_only": False,
                "backend_kind": "caller_driver",
                "adapter_id": "fixture-adapter",
                "adapter_generation": 1,
                "evidence_tier": "caller_observed",
            }
        },
    )
    try:
        assert host.caller_work is not None
        assert host.dispatcher is not None
        assert host.rlm_workbench is not None
        request = workbench_request(host)
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        repo = host.caller_work
        pending = repo.get(wait_for_pending_ticket(database))
        assert pending.root["request"]["method"] == "subagent.submit"

        def common(ticket, suffix):
            return caller_common_input(database, request, ticket, idempotency_key=suffix)

        claimed = repo.claim(
            {
                **common(pending, "real-cancel-claim"),
                "adapter_id": "fixture-adapter",
                "adapter_generation": 1,
                "claim_lease_ms": 5000,
            }
        )
        started = repo.mark_send_started(
            {
                **common(claimed, "real-cancel-send"),
                "claim_id": claimed.claimant.claim_id,
                "claim_fence": claimed.claimant.claim_fence,
                "physical_attempt_id": claimed.physical_attempt.physical_attempt_id,
                "expected_claim_expires_at_unix_ms": claimed.claimant.claim_expires_at_unix_ms,
                "provider_or_child_idempotency_key": (
                    claimed.physical_attempt.provider_or_child_idempotency_key
                ),
                "sent_request_digest": claimed.request_digest,
                "lookup_supported": True,
                "cancel_supported": True,
            }
        )
        command = {
            **common(started, "real-cancel-commit"),
            "claim_id": started.claimant.claim_id,
            "claim_fence": started.claimant.claim_fence,
            "physical_attempt_id": started.physical_attempt.physical_attempt_id,
            "sent_request_digest": started.request_digest,
            "sent_at_unix_ms": FIXED_NOW_MS + 2,
            "provider_or_child_request_id": "controlled-child-cancel",
            "model_response": None,
            "observation": {
                "kind": "subagent",
                "outcome": outcome,
                "child_execution_id": "controlled-child-execution",
                "result_digest": None,
                "artifact_manifest_digest": None,
                "host_receipt_digest": canonical_sha256("controlled cancellation"),
            },
        }
        CallerWorkCommitInput.model_validate(command, strict=True)
        settled = repo.commit(command)
        terminal = host.dispatcher.wait(
            admitted.operation, timeout_s=0.2 if outcome == "outcome_unknown" else 10
        )
        snapshot = host.rlm_workbench.snapshot(admitted.operation)
        if outcome == "outcome_unknown":
            assert settled.state == "outcome_unknown"
            # The existing unknown-receipt branch keeps the accepted job
            # waiting_external; uncertainty is retained on its actual ticket.
            # It must not settle/resume the cell or run the next planner.
            assert terminal.state.value == "accepted"
            assert snapshot.phase == "waiting_external"
            assert snapshot.pending_tickets and snapshot.result is None
            assert planner.calls == 1 and repo.send_started_count(pending.ticket_id) == 1
            with sqlite3.connect(database) as conn:
                assert conn.execute(
                    "SELECT COUNT(*) FROM rlm_workbench_cells WHERE state='committed'"
                ).fetchone() == (0,)
                assert conn.execute(
                    "SELECT COUNT(*) FROM rlm_workbench_artifact_stages"
                ).fetchone() == (0,)
                assert conn.execute(
                    "SELECT COUNT(*) FROM rlm_workbench_finalization_manifests"
                ).fetchone() == (0,)
            return
        assert settled.state == "cancelled_certain"
        assert terminal.state.value == "failed"
        assert snapshot.phase == "failed" and snapshot.certainty == "certain"
        assert terminal.failure is not None and terminal.failure.code == "WORKSPACE_FAILED"
        assert planner.calls == 2
        assert repo.send_started_count(pending.ticket_id) == 1
        assert not snapshot.pending_tickets and snapshot.result is None
        with sqlite3.connect(database) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM rlm_workbench_cells WHERE state='committed'"
            ).fetchone() == (1,)
            # A committed test-only stage proves both cell assertions succeeded;
            # it is not publication and is not an evaluation task candidate.
            assert conn.execute(
                "SELECT COUNT(*) FROM rlm_workbench_artifact_stages WHERE state='committed'"
            ).fetchone() == (1,)
            assert conn.execute(
                "SELECT COUNT(*) FROM rlm_workbench_finalization_manifests"
            ).fetchone() == (0,)
    finally:
        host.close()
