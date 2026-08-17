#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sqlite3
import tempfile
from pathlib import Path

from aar.runtime.registry import UnsupportedRegistrySchema

from generate_registry_v5_fixture import canonical_row_set_digest

EXPECTED_VERSION = "0.4.0a6"
EXPECTED_COMMIT = "a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90"
FIXED_NOW_MS = 2_000_000_000_000
ROOT = Path(__file__).resolve().parent


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical_digest(value: object) -> str:
    return digest_bytes(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )


def installed_distribution_binding() -> dict[str, object]:
    distribution = importlib.metadata.distribution("adaptive-agent-runtime")
    direct_url_text = distribution.read_text("direct_url.json")
    if direct_url_text is None:
        raise RuntimeError("installed distribution has no direct_url provenance")
    direct_url = json.loads(direct_url_text)
    vcs_info = direct_url.get("vcs_info", {})
    if vcs_info.get("commit_id") != EXPECTED_COMMIT:
        raise RuntimeError("installed distribution source commit mismatch")
    if vcs_info.get("requested_revision") != "v0.4.0a6":
        raise RuntimeError("installed distribution revision mismatch")
    inventory: list[dict[str, object]] = []
    for package_path in sorted(distribution.files or [], key=str):
        relative_path = package_path.as_posix()
        if package_path.suffix == ".pyc" or "__pycache__" in package_path.parts:
            continue
        resolved = Path(distribution.locate_file(package_path)).resolve()
        if not resolved.is_file():
            raise RuntimeError(f"installed distribution file missing: {relative_path}")
        raw = resolved.read_bytes()
        inventory.append(
            {
                "path": relative_path,
                "size_bytes": len(raw),
                "sha256": digest_bytes(raw),
            }
        )
    if not inventory:
        raise RuntimeError("installed distribution inventory is empty")
    payload: dict[str, object] = {
        "package_version": importlib.metadata.version("adaptive-agent-runtime"),
        "source_commit": EXPECTED_COMMIT,
        "requested_revision": "v0.4.0a6",
        "files": inventory,
    }
    return {**payload, "distribution_digest": canonical_digest(payload)}


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def sqlite_backup(source_path: Path, destination_path: Path) -> None:
    source = sqlite3.connect(source_path)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()


def database_tables(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def build_cutover_event(
    *,
    cutover_epoch: str,
    sequence: int,
    previous_event_digest: str | None,
    state: str,
    occurred_at_unix_ms: int,
    migration_attestation_digest: str | None,
    candidate_readback_digest: str | None,
    post_snapshot_barrier_digest: str | None,
    decision_digest: str | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "aar.migration-cutover-event-payload.v1",
        "cutover_epoch": cutover_epoch,
        "sequence": sequence,
        "previous_event_digest": previous_event_digest,
        "state": state,
        "occurred_at_unix_ms": occurred_at_unix_ms,
        "migration_attestation_digest": migration_attestation_digest,
        "candidate_readback_digest": candidate_readback_digest,
        "post_snapshot_barrier_digest": post_snapshot_barrier_digest,
        "decision_digest": decision_digest,
    }
    return {"event": payload, "event_digest": canonical_digest(payload)}


def validate_terminal_cutover_ledger(ledger: dict[str, object]) -> None:
    events = ledger.get("events")
    if not isinstance(events, list) or not events:
        raise RuntimeError("cutover ledger events missing")
    states: list[str] = []
    previous_digest: str | None = None
    previous_time = -1
    for sequence, document in enumerate(events):
        if not isinstance(document, dict):
            raise RuntimeError("cutover event document invalid")
        event = document.get("event")
        if not isinstance(event, dict):
            raise RuntimeError("cutover event payload invalid")
        if document.get("event_digest") != canonical_digest(event):
            raise RuntimeError("cutover event digest mismatch")
        if event.get("cutover_epoch") != ledger.get("cutover_epoch"):
            raise RuntimeError("cutover epoch mismatch")
        if event.get("sequence") != sequence:
            raise RuntimeError("cutover sequence mismatch")
        if event.get("previous_event_digest") != previous_digest:
            raise RuntimeError("cutover previous digest mismatch")
        occurred_at = event.get("occurred_at_unix_ms")
        if not isinstance(occurred_at, int) or occurred_at < previous_time:
            raise RuntimeError("cutover event time regression")
        previous_time = occurred_at
        state = event.get("state")
        if not isinstance(state, str):
            raise RuntimeError("cutover state invalid")
        states.append(state)
        previous_digest = str(document["event_digest"])
    allowed_paths = {
        ("prepared", "candidate_active", "rolled_back_before_write"),
        (
            "prepared",
            "candidate_active",
            "post_snapshot_write",
            "retired_forward",
        ),
    }
    if tuple(states) not in allowed_paths:
        raise RuntimeError(f"illegal cutover state path: {states}")
    if ledger.get("head_event_digest") != previous_digest:
        raise RuntimeError("cutover head digest mismatch")


def split_sql(value: str) -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in value.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise RuntimeError("incomplete migration SQL")
    return statements


def open_v5_host(database_path: Path) -> None:
    from aar.runtime.reference_host import ReferenceHost

    host = ReferenceHost(
        database_path,
        now_ms=lambda: FIXED_NOW_MS,
        programmable_backend="plain",
        enable_durable_dispatch=False,
    )
    host.close()


def create_database(path: Path, baseline_sql: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(baseline_sql)
    finally:
        connection.close()


def expect_integrity_error(
    connection: sqlite3.Connection,
    statement: str,
    parameters: tuple[object, ...] = (),
) -> None:
    connection.execute("SAVEPOINT constraint_probe")
    rejected = False
    try:
        connection.execute(statement, parameters)
    except sqlite3.IntegrityError:
        rejected = True
    finally:
        connection.execute("ROLLBACK TO constraint_probe")
        connection.execute("RELEASE constraint_probe")
    if not rejected:
        raise RuntimeError("migration constraint probe unexpectedly succeeded")


def insert_row(
    connection: sqlite3.Connection,
    table: str,
    values: dict[str, object],
) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    connection.execute(
        f"INSERT INTO {table}({columns}) VALUES({placeholders})",
        tuple(values.values()),
    )


def _verify_caller_ticket_constraints(connection: sqlite3.Connection) -> None:
    insert_row(
        connection,
        "operations",
        {
            "operation_id": "op-ticket-probe",
            "host_value": "host",
            "principal_value": "principal",
            "idempotency_key": "idem-ticket-probe",
            "input_digest": "sha256:" + "1" * 64,
            "state": "accepted",
            "certainty": "certain",
            "runtime_generation": 1,
            "record_revision": 0,
            "reconciliation_required": 0,
            "request_json": "{}",
            "payload_json": "{}",
            "created_at_unix_ms": FIXED_NOW_MS,
            "updated_at_unix_ms": FIXED_NOW_MS,
        },
    )
    insert_row(
        connection,
        "rlm_workbench_jobs",
        {
            "operation_id": "op-ticket-probe",
            "phase": "waiting_external",
            "control_revision": 1,
            "cancellation_revision": 0,
            "cancellation_requested": 0,
            "spec_json": "{}",
            "spec_digest": "sha256:" + "2" * 64,
            "context_digest": "sha256:" + "3" * 64,
            "route_binding_digest": "sha256:" + "4" * 64,
            "cumulative_deadline_unix_ms": FIXED_NOW_MS + 60_000,
            "certainty": "certain",
            "created_at_unix_ms": FIXED_NOW_MS,
            "updated_at_unix_ms": FIXED_NOW_MS,
        },
    )
    insert_row(
        connection,
        "rlm_workbench_suspensions",
        {
            "operation_id": "op-ticket-probe",
            "suspension_revision": 1,
            "control_revision": 1,
            "logical_owner_json": "{}",
            "logical_owner_digest": "sha256:" + "5" * 64,
            "broker_method": "model.request",
            "contract_id": "aar.broker-contract.model-request.v2",
            "request_digest": "sha256:" + "6" * 64,
            "ticket_id": "ticket-probe",
            "state": "pending",
            "created_at_unix_ms": FIXED_NOW_MS,
        },
    )
    insert_row(
        connection,
        "caller_work_tickets",
        {
            "ticket_id": "ticket-probe",
            "operation_id": "op-ticket-probe",
            "suspension_revision": 1,
            "revision": 0,
            "ticket_digest": "sha256:" + "7" * 64,
            "request_json": "{}",
            "request_digest": "sha256:" + "6" * 64,
            "method": "model.request",
            "contract_id": "aar.broker-contract.model-request.v2",
            "logical_owner_json": "{}",
            "state": "pending",
            "deadline_unix_ms": FIXED_NOW_MS + 60_000,
            "created_at_unix_ms": FIXED_NOW_MS,
            "updated_at_unix_ms": FIXED_NOW_MS,
        },
    )
    expect_integrity_error(
        connection,
        "UPDATE caller_work_tickets SET method='subagent.submit' "
        "WHERE ticket_id='ticket-probe'",
    )
    expect_integrity_error(
        connection,
        "UPDATE caller_work_tickets SET send_started_at_unix_ms=? WHERE ticket_id=?",
        (FIXED_NOW_MS, "ticket-probe"),
    )
    connection.execute("SAVEPOINT unclaimed_cancel_probe")
    connection.execute(
        "UPDATE caller_work_tickets SET revision=1, state='cancelled_before_send', "
        "settled_receipt_digest=?, settled_at_unix_ms=? WHERE ticket_id='ticket-probe'",
        ("sha256:" + "a" * 64, FIXED_NOW_MS + 1),
    )
    expect_integrity_error(
        connection,
        "UPDATE caller_work_tickets SET send_started_at_unix_ms=? "
        "WHERE ticket_id='ticket-probe'",
        (FIXED_NOW_MS,),
    )
    connection.execute("ROLLBACK TO unclaimed_cancel_probe")
    connection.execute("RELEASE unclaimed_cancel_probe")
    connection.execute(
        "UPDATE caller_work_tickets SET "
        "revision=1, state='send_reserved', claimant_principal_id='principal', "
        "claimant_session_id='session', adapter_id='adapter', adapter_generation=1, "
        "claim_id='claim', claim_fence='fence', claim_expires_at_unix_ms=?, "
        "physical_attempt_id='attempt', external_idempotency_key='external-idem', "
        "lookup_supported=1, cancel_supported=1 WHERE ticket_id='ticket-probe'",
        (FIXED_NOW_MS + 30_000,),
    )
    connection.execute("SAVEPOINT reserved_cancel_probe")
    connection.execute(
        "UPDATE caller_work_tickets SET revision=2, state='cancelled_before_send', "
        "settled_receipt_digest=?, settled_at_unix_ms=? WHERE ticket_id='ticket-probe'",
        ("sha256:" + "a" * 64, FIXED_NOW_MS + 1),
    )
    expect_integrity_error(
        connection,
        "UPDATE caller_work_tickets SET send_started_at_unix_ms=? "
        "WHERE ticket_id='ticket-probe'",
        (FIXED_NOW_MS,),
    )
    connection.execute("ROLLBACK TO reserved_cancel_probe")
    connection.execute("RELEASE reserved_cancel_probe")
    connection.execute(
        "UPDATE caller_work_tickets SET "
        "revision=2, state='send_started', claimant_principal_id='principal', "
        "claimant_session_id='session', adapter_id='adapter', adapter_generation=1, "
        "claim_id='claim', claim_fence='fence', claim_expires_at_unix_ms=?, "
        "physical_attempt_id='attempt', external_idempotency_key='external-idem', "
        "lookup_supported=1, cancel_supported=1, send_started_at_unix_ms=?, "
        "sent_request_digest=? WHERE ticket_id='ticket-probe'",
        (FIXED_NOW_MS + 30_000, FIXED_NOW_MS, "sha256:" + "8" * 64),
    )
    expect_integrity_error(
        connection,
        "UPDATE caller_work_tickets SET settled_receipt_digest=?, "
        "settled_at_unix_ms=? WHERE ticket_id='ticket-probe'",
        ("sha256:" + "9" * 64, FIXED_NOW_MS + 1),
    )
    connection.execute(
        "UPDATE caller_work_tickets SET revision=2, state='cancelled_certain', "
        "settled_receipt_digest=?, settled_at_unix_ms=? WHERE ticket_id='ticket-probe'",
        ("sha256:" + "9" * 64, FIXED_NOW_MS + 1),
    )
    expect_integrity_error(
        connection,
        "UPDATE caller_work_tickets SET settled_receipt_digest=NULL "
        "WHERE ticket_id='ticket-probe'",
    )


def verify_caller_ticket_constraints(connection: sqlite3.Connection) -> None:
    connection.execute("SAVEPOINT caller_ticket_state_probes")
    try:
        _verify_caller_ticket_constraints(connection)
    finally:
        connection.execute("ROLLBACK TO caller_ticket_state_probes")
        connection.execute("RELEASE caller_ticket_state_probes")


def _verify_rebind_constraints(connection: sqlite3.Connection) -> None:
    _verify_caller_ticket_constraints(connection)
    for attempt_no, attempt_id, state in (
        (1, "rebind-prior-attempt", "suspended"),
        (2, "rebind-successor-attempt", "accepted"),
    ):
        insert_row(
            connection,
            "operation_attempts",
            {
                "operation_id": "op-ticket-probe",
                "attempt_no": attempt_no,
                "attempt_id": attempt_id,
                "runtime_generation": 1,
                "dispatcher_generation": 1,
                "state": state,
                "certainty": "certain",
                "created_at_unix_ms": FIXED_NOW_MS,
            },
        )
    insert_row(
        connection,
        "rlm_workbench_cells",
        {
            "operation_id": "op-ticket-probe",
            "cell_execution_id": "rebind-cell",
            "cell_index": 0,
            "source_json": "{}",
            "source_digest": "sha256:" + "1" * 64,
            "state": "suspended",
            "attempt_id": "rebind-prior-attempt",
            "attempt_fence": "sha256:" + "2" * 64,
            "workspace_id": "rebind-workspace",
            "workspace_generation": 1,
            "pre_workspace_revision": 0,
            "created_at_unix_ms": FIXED_NOW_MS,
            "updated_at_unix_ms": FIXED_NOW_MS,
        },
    )
    settlement_digest = "sha256:" + "3" * 64
    outbox_digest = "sha256:" + "4" * 64
    token_digest = "sha256:" + "5" * 64
    insert_row(
        connection,
        "rlm_workbench_successor_outbox",
        {
            "operation_id": "op-ticket-probe",
            "suspension_revision": 1,
            "settlement_digest": settlement_digest,
            "outbox_digest": outbox_digest,
            "state": "pending",
            "rebind_generation": 0,
            "created_at_unix_ms": FIXED_NOW_MS,
        },
    )
    insert_row(
        connection,
        "rlm_workbench_attempt_authority",
        {
            "operation_id": "op-ticket-probe",
            "attempt_id": "rebind-prior-attempt",
            "attempt_fence": "sha256:" + "2" * 64,
            "authority_generation": 1,
            "worker_owner_generation": 1,
            "worker_process_identity_digest": "sha256:" + "6" * 64,
            "workspace_id": "rebind-workspace",
            "workspace_generation": 1,
            "workspace_revision": 0,
            "cell_execution_id": "rebind-cell",
            "updated_at_unix_ms": FIXED_NOW_MS,
        },
    )
    connection.execute(
        "UPDATE rlm_workbench_successor_outbox SET state='prepared', "
        "rebind_generation=1, successor_attempt_id='rebind-successor-attempt', "
        "successor_attempt_fence=?, prepared_at_unix_ms=? "
        "WHERE operation_id='op-ticket-probe' AND suspension_revision=1",
        ("sha256:" + "7" * 64, FIXED_NOW_MS + 1),
    )
    insert_row(
        connection,
        "rlm_workbench_rebind_transfers",
        {
            "token_digest": token_digest,
            "operation_id": "op-ticket-probe",
            "suspension_revision": 1,
            "settlement_digest": settlement_digest,
            "outbox_digest": outbox_digest,
            "rebind_generation": 1,
            "prior_authority_generation": 1,
            "successor_authority_generation": 2,
            "expected_control_revision": 1,
            "expected_cancellation_revision": 0,
            "expected_cumulative_deadline_unix_ms": FIXED_NOW_MS + 60_000,
            "prior_attempt_id": "rebind-prior-attempt",
            "prior_attempt_fence": "sha256:" + "2" * 64,
            "successor_attempt_id": "rebind-successor-attempt",
            "successor_attempt_fence": "sha256:" + "7" * 64,
            "worker_owner_generation": 2,
            "worker_process_identity_digest": "sha256:" + "8" * 64,
            "workspace_id": "rebind-workspace",
            "workspace_generation": 1,
            "workspace_revision": 0,
            "cell_execution_id": "rebind-cell",
            "expires_at_unix_ms": FIXED_NOW_MS + 30_000,
            "state": "prepared",
            "prepared_at_unix_ms": FIXED_NOW_MS + 1,
        },
    )
    connection.execute("SAVEPOINT rebind_commit_crash")
    connection.execute(
        "UPDATE rlm_workbench_rebind_transfers SET state='committed', "
        "committed_at_unix_ms=? WHERE token_digest=? AND state='prepared'",
        (FIXED_NOW_MS + 2, token_digest),
    )
    connection.execute(
        "UPDATE rlm_workbench_attempt_authority SET "
        "attempt_id='rebind-successor-attempt', attempt_fence=?, "
        "authority_generation=2, worker_owner_generation=2, "
        "worker_process_identity_digest=?, rebind_token_digest=?, "
        "updated_at_unix_ms=? WHERE operation_id='op-ticket-probe' "
        "AND attempt_id='rebind-prior-attempt' AND authority_generation=1",
        ("sha256:" + "7" * 64, "sha256:" + "8" * 64, token_digest, FIXED_NOW_MS + 2),
    )
    connection.execute(
        "UPDATE rlm_workbench_successor_outbox SET state='consumed', "
        "consumed_at_unix_ms=? WHERE operation_id='op-ticket-probe' "
        "AND suspension_revision=1 AND state='prepared'",
        (FIXED_NOW_MS + 2,),
    )
    connection.execute("ROLLBACK TO rebind_commit_crash")
    connection.execute("RELEASE rebind_commit_crash")
    crash_state = connection.execute(
        "SELECT o.state, t.state, a.attempt_id, a.authority_generation "
        "FROM rlm_workbench_successor_outbox o "
        "JOIN rlm_workbench_rebind_transfers t USING(operation_id, suspension_revision) "
        "JOIN rlm_workbench_attempt_authority a USING(operation_id) "
        "WHERE o.operation_id='op-ticket-probe'"
    ).fetchone()
    if crash_state != ("prepared", "prepared", "rebind-prior-attempt", 1):
        raise RuntimeError("pre-commit rebind crash did not preserve predecessor authority")
    connection.execute("SAVEPOINT rebind_commit")
    connection.execute(
        "UPDATE rlm_workbench_rebind_transfers SET state='committed', "
        "committed_at_unix_ms=? WHERE token_digest=? AND state='prepared'",
        (FIXED_NOW_MS + 2, token_digest),
    )
    connection.execute(
        "UPDATE rlm_workbench_attempt_authority SET "
        "attempt_id='rebind-successor-attempt', attempt_fence=?, "
        "authority_generation=2, worker_owner_generation=2, "
        "worker_process_identity_digest=?, rebind_token_digest=?, "
        "updated_at_unix_ms=? WHERE operation_id='op-ticket-probe' "
        "AND attempt_id='rebind-prior-attempt' AND authority_generation=1",
        ("sha256:" + "7" * 64, "sha256:" + "8" * 64, token_digest, FIXED_NOW_MS + 2),
    )
    connection.execute(
        "UPDATE rlm_workbench_successor_outbox SET state='consumed', "
        "consumed_at_unix_ms=? WHERE operation_id='op-ticket-probe' "
        "AND suspension_revision=1 AND state='prepared'",
        (FIXED_NOW_MS + 2,),
    )
    connection.execute("RELEASE rebind_commit")
    committed_state = connection.execute(
        "SELECT o.state, t.state, a.attempt_id, a.authority_generation, "
        "a.rebind_token_digest FROM rlm_workbench_successor_outbox o "
        "JOIN rlm_workbench_rebind_transfers t USING(operation_id, suspension_revision) "
        "JOIN rlm_workbench_attempt_authority a USING(operation_id) "
        "WHERE o.operation_id='op-ticket-probe'"
    ).fetchone()
    if committed_state != (
        "consumed",
        "committed",
        "rebind-successor-attempt",
        2,
        token_digest,
    ):
        raise RuntimeError("rebind commit did not atomically install successor authority")
    expect_integrity_error(
        connection,
        "UPDATE rlm_workbench_rebind_transfers SET state='aborted', "
        "aborted_at_unix_ms=? WHERE token_digest=?",
        (FIXED_NOW_MS + 3, token_digest),
    )
    expect_integrity_error(
        connection,
        "UPDATE rlm_workbench_rebind_transfers SET state='consumed', "
        "consumption_kind='recovery_fenced_loss', ack_digest=?, "
        "consumed_at_unix_ms=? WHERE token_digest=?",
        ("sha256:" + "9" * 64, FIXED_NOW_MS + 3, token_digest),
    )
    connection.execute(
        "UPDATE rlm_workbench_rebind_transfers SET state='consumed', "
        "consumption_kind='worker_ack', ack_digest=?, consumed_at_unix_ms=? "
        "WHERE token_digest=? AND state='committed'",
        ("sha256:" + "9" * 64, FIXED_NOW_MS + 3, token_digest),
    )


def verify_rebind_constraints(connection: sqlite3.Connection) -> None:
    connection.execute("SAVEPOINT rebind_state_probes")
    try:
        _verify_rebind_constraints(connection)
    finally:
        connection.execute("ROLLBACK TO rebind_state_probes")
        connection.execute("RELEASE rebind_state_probes")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-sql", type=Path, required=True)
    parser.add_argument("--migration-sql", type=Path, required=True)
    args = parser.parse_args()
    version = importlib.metadata.version("adaptive-agent-runtime")
    if version != EXPECTED_VERSION:
        raise RuntimeError(f"expected {EXPECTED_VERSION}, got {version}")
    baseline_sql = args.baseline_sql.read_text(encoding="utf-8")
    baseline_sql_digest = digest_bytes(baseline_sql.encode("utf-8"))
    baseline_binding_path = args.baseline_sql.with_name("registry-v5-binding.json")
    baseline_binding = json.loads(baseline_binding_path.read_text(encoding="utf-8"))
    if baseline_binding.get("sql_dump_sha256") != baseline_sql_digest:
        raise RuntimeError("baseline SQL digest mismatch")
    installed_distribution = installed_distribution_binding()
    if baseline_binding.get("source_commit") != EXPECTED_COMMIT:
        raise RuntimeError("baseline source commit mismatch")
    contract_manifest = json.loads(
        (ROOT / "contracts" / "contract-manifest.json").read_text(encoding="utf-8")
    )
    contract_manifest_digest = str(contract_manifest["manifest_digest"])
    migration_sql = args.migration_sql.read_text(encoding="utf-8")
    migration_digest = digest_bytes(migration_sql.encode("utf-8"))
    statements = split_sql(migration_sql)

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        baseline = root / "baseline.sqlite"
        create_database(baseline, baseline_sql)
        baseline_connection = sqlite3.connect(baseline)
        try:
            baseline_connection.execute("PRAGMA foreign_keys=ON")
            v5_tables = database_tables(baseline_connection)
            v5_data_tables = [
                table for table in v5_tables if table != "schema_migrations"
            ]
            v5_host_stable_tables = [
                table for table in v5_data_tables if table != "runtime_meta"
            ]
            initial_data_digest = canonical_row_set_digest(
                baseline_connection,
                v5_data_tables,
            )
            initial_host_stable_data_digest = canonical_row_set_digest(
                baseline_connection,
                v5_host_stable_tables,
            )
            runtime_meta_row = baseline_connection.execute(
                "SELECT singleton, generation FROM runtime_meta"
            ).fetchone()
            if runtime_meta_row is None:
                raise RuntimeError("baseline runtime_meta row missing")
            initial_runtime_generation = int(runtime_meta_row[1])
        finally:
            baseline_connection.close()
        if initial_data_digest != baseline_binding.get(
            "canonical_data_row_set_digest"
        ):
            raise RuntimeError("populated v5 fixture row-set binding mismatch")
        open_v5_host(baseline)
        baseline_connection = sqlite3.connect(baseline)
        try:
            host_reopen_stable_data_digest = canonical_row_set_digest(
                baseline_connection,
                v5_host_stable_tables,
            )
            host_runtime_meta_row = baseline_connection.execute(
                "SELECT singleton, generation FROM runtime_meta"
            ).fetchone()
            if host_runtime_meta_row is None:
                raise RuntimeError("reopened runtime_meta row missing")
            host_runtime_generation = int(host_runtime_meta_row[1])
        finally:
            baseline_connection.close()
        if host_reopen_stable_data_digest != initial_host_stable_data_digest:
            raise RuntimeError("v5 host reopen changed stable populated baseline rows")
        if host_runtime_generation != initial_runtime_generation + 1:
            raise RuntimeError("v5 host reopen runtime generation transition mismatch")
        wal_connection = sqlite3.connect(baseline)
        wal_connection.execute("PRAGMA foreign_keys=ON")
        journal_mode = str(
            wal_connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        ).lower()
        if journal_mode != "wal":
            raise RuntimeError("failed to enable WAL fixture")
        wal_connection.execute("PRAGMA wal_autocheckpoint=0")
        wal_connection.execute(
            "INSERT INTO asset_events(event_digest, event_kind, episode_digest, event_json) "
            "VALUES(?, ?, ?, ?)",
            (
                "sha256:" + "8" * 64,
                "fixture.wal-committed",
                None,
                '{"kind":"fixture.wal-committed"}',
            ),
        )
        wal_connection.commit()
        wal_path = Path(f"{baseline}-wal")
        if not wal_path.is_file() or wal_path.stat().st_size == 0:
            raise RuntimeError("WAL fixture has no committed WAL pages")
        source_data_digest = canonical_row_set_digest(
            wal_connection,
            v5_data_tables,
        )
        cutover_epoch = "cutover-v6-fixture"
        prepared_event = build_cutover_event(
            cutover_epoch=cutover_epoch,
            sequence=0,
            previous_event_digest=None,
            state="prepared",
            occurred_at_unix_ms=FIXED_NOW_MS,
            migration_attestation_digest=None,
            candidate_readback_digest=None,
            post_snapshot_barrier_digest=None,
            decision_digest=None,
        )
        prepared_digest = str(prepared_event["event_digest"])
        backup = root / "backup.sqlite"
        sqlite_backup(baseline, backup)
        backup_connection = sqlite3.connect(backup)
        try:
            backup_connection.execute("PRAGMA foreign_keys=ON")
            if backup_connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("SQLite backup snapshot integrity failed")
            if backup_connection.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("SQLite backup snapshot foreign-key check failed")
            backup_data_digest = canonical_row_set_digest(
                backup_connection,
                v5_data_tables,
            )
        finally:
            backup_connection.close()
            wal_connection.close()
        if backup_data_digest != source_data_digest:
            raise RuntimeError("SQLite backup omitted committed WAL data")
        backup_digest = digest_file(backup)
        backup_size_bytes = backup.stat().st_size

        interrupted = root / "interrupted.sqlite"
        sqlite_backup(backup, interrupted)
        connection = sqlite3.connect(interrupted)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for statement in statements[: max(1, len(statements) // 2)]:
            connection.execute(statement)
        connection.close()
        open_v5_host(interrupted)

        migrated = root / "migrated.sqlite"
        sqlite_backup(backup, migrated)
        wheel_digest = digest_bytes(b"fixture-candidate-wheel")
        profile_digest = digest_bytes(b"fixture-profile")
        skill_digest = digest_bytes(b"fixture-skill")
        attestation_payload = {
            "schema_version": "aar.migration-v6-attestation-payload.v1",
            "migration_version": 6,
            "cutover_epoch": cutover_epoch,
            "snapshot_id": "snapshot-v5-fixture",
            "snapshot_sha256": backup_digest,
            "snapshot_size_bytes": backup_size_bytes,
            "canonical_v5_row_set_digest": source_data_digest,
            "source_commit": EXPECTED_COMMIT,
            "wheel_digest": wheel_digest,
            "profile_digest": profile_digest,
            "skill_digest": skill_digest,
            "contract_manifest_digest": contract_manifest_digest,
            "migration_sql_digest": migration_digest,
            "external_authority_store_id": "cutover-authority-fixture",
            "external_authority_prepared_digest": prepared_digest,
            "started_at_unix_ms": FIXED_NOW_MS + 1,
            "completed_at_unix_ms": FIXED_NOW_MS + 2,
            "foreign_key_violation_count": 0,
            "integrity_result": "ok",
        }
        attestation_digest = canonical_digest(attestation_payload)
        connection = sqlite3.connect(migrated)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations("
                "version, applied_at_unix_ms, migration_digest"
                ") VALUES(6, ?, ?)",
                (FIXED_NOW_MS, migration_digest),
            )
            insert_row(
                connection,
                "migration_v6_attestations",
                {
                    "migration_version": 6,
                    "attestation_digest": attestation_digest,
                    "cutover_epoch": cutover_epoch,
                    "snapshot_id": "snapshot-v5-fixture",
                    "snapshot_sha256": backup_digest,
                    "snapshot_size_bytes": backup_size_bytes,
                    "canonical_v5_row_set_digest": source_data_digest,
                    "source_commit": EXPECTED_COMMIT,
                    "wheel_digest": wheel_digest,
                    "profile_digest": profile_digest,
                    "skill_digest": skill_digest,
                    "contract_manifest_digest": contract_manifest_digest,
                    "migration_sql_digest": migration_digest,
                    "external_authority_store_id": "cutover-authority-fixture",
                    "external_authority_prepared_digest": prepared_digest,
                    "started_at_unix_ms": FIXED_NOW_MS + 1,
                    "completed_at_unix_ms": FIXED_NOW_MS + 2,
                    "foreign_key_violation_count": 0,
                    "integrity_result": "ok",
                },
            )
            caller_ticket_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(caller_work_tickets)"
                ).fetchall()
            }
            required_caller_ticket_columns = {
                "send_started_at_unix_ms",
                "sent_request_digest",
                "sent_at_unix_ms",
                "provider_or_child_request_id",
            }
            if not required_caller_ticket_columns <= caller_ticket_columns:
                missing = sorted(required_caller_ticket_columns - caller_ticket_columns)
                raise RuntimeError(f"caller_work_tickets columns missing: {missing}")
            verify_caller_ticket_constraints(connection)
            verify_rebind_constraints(connection)
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("migrated registry foreign-key check failed")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("migrated registry integrity check failed")
            post_migration_data_digest = canonical_row_set_digest(
                connection,
                v5_data_tables,
            )
            if post_migration_data_digest != source_data_digest:
                raise RuntimeError("migration changed populated v5 data rows")
            attestation_row = connection.execute(
                "SELECT attestation_digest, migration_sql_digest, snapshot_sha256 "
                "FROM migration_v6_attestations WHERE migration_version=6"
            ).fetchone()
            if attestation_row != (
                attestation_digest,
                migration_digest,
                backup_digest,
            ):
                raise RuntimeError("migration attestation row mismatch")
            connection.commit()
        finally:
            connection.close()

        candidate_readback_digest = digest_bytes(b"fixture-candidate-readback")
        candidate_event = build_cutover_event(
            cutover_epoch=cutover_epoch,
            sequence=1,
            previous_event_digest=prepared_digest,
            state="candidate_active",
            occurred_at_unix_ms=FIXED_NOW_MS + 3,
            migration_attestation_digest=attestation_digest,
            candidate_readback_digest=candidate_readback_digest,
            post_snapshot_barrier_digest=None,
            decision_digest=None,
        )
        rollback_event = build_cutover_event(
            cutover_epoch=cutover_epoch,
            sequence=2,
            previous_event_digest=str(candidate_event["event_digest"]),
            state="rolled_back_before_write",
            occurred_at_unix_ms=FIXED_NOW_MS + 4,
            migration_attestation_digest=attestation_digest,
            candidate_readback_digest=candidate_readback_digest,
            post_snapshot_barrier_digest=None,
            decision_digest=digest_bytes(b"fixture-pre-write-rollback-decision"),
        )
        rollback_ledger: dict[str, object] = {
            "schema_version": "aar.migration-cutover-authority.v1",
            "authority_store_id": "cutover-authority-fixture",
            "cutover_epoch": cutover_epoch,
            "snapshot_id": "snapshot-v5-fixture",
            "snapshot_sha256": backup_digest,
            "source_commit": EXPECTED_COMMIT,
            "wheel_digest": wheel_digest,
            "events": [prepared_event, candidate_event, rollback_event],
            "head_event_digest": rollback_event["event_digest"],
        }
        validate_terminal_cutover_ledger(rollback_ledger)
        post_snapshot_barrier_digest = digest_bytes(b"fixture-post-write-barrier")
        post_write_event = build_cutover_event(
            cutover_epoch=cutover_epoch,
            sequence=2,
            previous_event_digest=str(candidate_event["event_digest"]),
            state="post_snapshot_write",
            occurred_at_unix_ms=FIXED_NOW_MS + 4,
            migration_attestation_digest=attestation_digest,
            candidate_readback_digest=candidate_readback_digest,
            post_snapshot_barrier_digest=post_snapshot_barrier_digest,
            decision_digest=None,
        )
        retired_event = build_cutover_event(
            cutover_epoch=cutover_epoch,
            sequence=3,
            previous_event_digest=str(post_write_event["event_digest"]),
            state="retired_forward",
            occurred_at_unix_ms=FIXED_NOW_MS + 5,
            migration_attestation_digest=attestation_digest,
            candidate_readback_digest=candidate_readback_digest,
            post_snapshot_barrier_digest=post_snapshot_barrier_digest,
            decision_digest=digest_bytes(b"fixture-forward-retirement-decision"),
        )
        forward_ledger: dict[str, object] = {
            **rollback_ledger,
            "events": [prepared_event, candidate_event, post_write_event, retired_event],
            "head_event_digest": retired_event["event_digest"],
        }
        validate_terminal_cutover_ledger(forward_ledger)
        illegal_rollback = build_cutover_event(
            cutover_epoch=cutover_epoch,
            sequence=3,
            previous_event_digest=str(post_write_event["event_digest"]),
            state="rolled_back_before_write",
            occurred_at_unix_ms=FIXED_NOW_MS + 5,
            migration_attestation_digest=attestation_digest,
            candidate_readback_digest=candidate_readback_digest,
            post_snapshot_barrier_digest=None,
            decision_digest=digest_bytes(b"illegal-post-write-rollback"),
        )
        illegal_ledger: dict[str, object] = {
            **rollback_ledger,
            "events": [prepared_event, candidate_event, post_write_event, illegal_rollback],
            "head_event_digest": illegal_rollback["event_digest"],
        }
        rollback_after_post_write_rejected = False
        try:
            validate_terminal_cutover_ledger(illegal_ledger)
        except RuntimeError:
            rollback_after_post_write_rejected = True
        if not rollback_after_post_write_rejected:
            raise RuntimeError("post-write snapshot rollback was not rejected")

        expected_rejection = "registry schema version 6 is newer than supported version 5"
        try:
            open_v5_host(migrated)
        except UnsupportedRegistrySchema as error:
            if str(error) != expected_rejection:
                raise RuntimeError("v0.4.0a6 rejected v6 for the wrong schema reason") from error
        except Exception as error:
            raise RuntimeError("v0.4.0a6 failed to open v6 for a non-schema reason") from error
        else:
            raise RuntimeError("v0.4.0a6 unexpectedly opened registry schema v6")

        restored = root / "restored.sqlite"
        sqlite_backup(backup, restored)
        open_v5_host(restored)

    result = {
        "schema_version": "aar.sdd-registry-v5-migration-verification.v1",
        "package_version": version,
        "source_commit": EXPECTED_COMMIT,
        "requested_revision": installed_distribution["requested_revision"],
        "installed_distribution_digest": installed_distribution["distribution_digest"],
        "installed_distribution_file_count": len(installed_distribution["files"]),
        "baseline_sql_digest": baseline_sql_digest,
        "baseline_binding_file_digest": digest_file(baseline_binding_path),
        "contract_manifest_digest": contract_manifest_digest,
        "verifier_sha256": digest_file(Path(__file__)),
        "migration_digest": migration_digest,
        "statement_count": len(statements),
        "mid_transaction_rollback_reopened_by_v5": True,
        "committed_v6_rejected_by_v5": True,
        "v5_newer_schema_rejection_reason_verified": True,
        "migrated_integrity_and_fk_checked_before_v5_rejection": True,
        "restored_backup_reopened_by_v5": True,
        "caller_ticket_state_constraints_enforced": True,
        "caller_ticket_pre_send_cancellation_enforced": True,
        "caller_ticket_suspension_binding_enforced": True,
        "rebind_prepare_abort_constraints_enforced": True,
        "rebind_commit_atomic_authority_verified": True,
        "rebind_post_commit_replay_classification_verified": True,
        "sqlite_backup_api_verified": True,
        "wal_committed_rows_included_in_snapshot": True,
        "populated_v5_data_rows_preserved": True,
        "v5_host_reopen_stable_rows_preserved": True,
        "v5_host_reopen_runtime_generation_increment_verified": True,
        "migration_attestation_row_verified": True,
        "migration_and_attestation_atomic_transaction_verified": True,
        "pre_write_snapshot_rollback_path_verified": True,
        "forward_retirement_path_verified": True,
        "rollback_after_post_snapshot_write_rejected": True,
        "snapshot_sha256": backup_digest,
        "snapshot_size_bytes": backup_size_bytes,
        "canonical_v5_data_row_set_digest": source_data_digest,
        "baseline_fixture_data_row_set_digest": initial_data_digest,
        "pre_migration_v5_data_row_set_digest": source_data_digest,
        "post_migration_v5_data_row_set_digest": post_migration_data_digest,
        "migration_attestation_digest": attestation_digest,
        "forward_cutover_ledger_digest": canonical_digest(forward_ledger),
        "rollback_cutover_ledger_digest": canonical_digest(rollback_ledger),
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
