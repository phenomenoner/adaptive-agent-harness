#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sqlite3
import tempfile
from pathlib import Path

EXPECTED_VERSION = "0.4.0a6"
EXPECTED_COMMIT = "a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90"
FIXED_NOW_MS = 2_000_000_000_000


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical_row_set_digest(
    connection: sqlite3.Connection,
    tables: list[str],
) -> str:
    payload: list[dict[str, object]] = []
    for table in sorted(tables):
        columns = [
            str(row[1])
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        ]
        encoded_rows: list[list[object]] = []
        for row in connection.execute(f'SELECT * FROM "{table}"'):
            encoded_rows.append(
                [
                    {"bytes_hex": bytes(value).hex()}
                    if isinstance(value, bytes)
                    else value
                    for value in row
                ]
            )
        encoded_rows.sort(
            key=lambda value: json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        payload.append(
            {"table": table, "columns": columns, "rows": encoded_rows}
        )
    return digest_bytes(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )


def populate_representative_rows(connection: sqlite3.Connection) -> None:
    operation_id = "op-v5-populated-fixture"
    connection.execute(
        "INSERT INTO operations("
        "operation_id, host_value, principal_value, idempotency_key, input_digest, "
        "state, certainty, runtime_generation, record_revision, reconciliation_required, "
        "request_json, payload_json, result_json, created_at_unix_ms, updated_at_unix_ms"
        ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            operation_id,
            "fixture-host",
            "fixture-principal",
            "fixture-idempotency",
            "sha256:" + "1" * 64,
            "succeeded",
            "certain",
            1,
            3,
            0,
            '{"kind":"fixture-request"}',
            '{"kind":"fixture-payload"}',
            '{"ok":true}',
            FIXED_NOW_MS - 1000,
            FIXED_NOW_MS,
        ),
    )
    connection.execute(
        "INSERT INTO operation_attempts("
        "operation_id, attempt_no, attempt_id, runtime_generation, "
        "dispatcher_generation, state, certainty, created_at_unix_ms, "
        "started_at_unix_ms, ended_at_unix_ms, checkpoint_digest"
        ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            operation_id,
            1,
            "attempt-v5-populated-fixture",
            1,
            1,
            "succeeded",
            "certain",
            FIXED_NOW_MS - 900,
            FIXED_NOW_MS - 800,
            FIXED_NOW_MS - 100,
            "sha256:" + "2" * 64,
        ),
    )
    connection.execute(
        "INSERT INTO operation_leases("
        "operation_id, attempt_no, lease_epoch, owner_digest, acquired_at_unix_ms, "
        "heartbeat_at_unix_ms, expires_at_unix_ms, released_at_unix_ms"
        ") VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (
            operation_id,
            1,
            1,
            "sha256:" + "3" * 64,
            FIXED_NOW_MS - 800,
            FIXED_NOW_MS - 200,
            FIXED_NOW_MS + 30_000,
            FIXED_NOW_MS - 100,
        ),
    )
    connection.execute(
        "INSERT INTO rlm_jobs(operation_id, state, spec_json, result_json) "
        "VALUES(?, ?, ?, ?)",
        (
            operation_id,
            "succeeded",
            '{"max_steps":1,"query":"fixture","strategy":"baseline"}',
            '{"answer":"fixture"}',
        ),
    )
    connection.execute(
        "INSERT INTO rlm_steps(operation_id, step_index, step_json) VALUES(?, ?, ?)",
        (operation_id, 0, '{"kind":"evidence.query","status":"succeeded"}'),
    )
    connection.execute(
        "INSERT INTO workspaces(workspace_id, session_id, generation, revision, state_json) "
        "VALUES(?, ?, ?, ?, ?)",
        (
            "workspace-v5-populated-fixture",
            "session-v5-populated-fixture",
            1,
            2,
            '{"fixture":true}',
        ),
    )
    connection.execute(
        "INSERT INTO workspace_receipts(operation_id, workspace_id, result_json) "
        "VALUES(?, ?, ?)",
        (
            operation_id,
            "workspace-v5-populated-fixture",
            '{"revision":2,"status":"succeeded"}',
        ),
    )
    connection.execute(
        "INSERT INTO asset_bodies(body_digest, kind, schema_version, body_json) "
        "VALUES(?, ?, ?, ?)",
        (
            "sha256:" + "4" * 64,
            "fixture",
            "fixture.v1",
            '{"value":"fixture"}',
        ),
    )
    connection.execute(
        "INSERT INTO asset_manifests("
        "manifest_digest, kind, body_digest, document_json"
        ") VALUES(?, ?, ?, ?)",
        (
            "sha256:" + "5" * 64,
            "fixture",
            "sha256:" + "4" * 64,
            '{"body_digest":"sha256:4444444444444444444444444444444444444444444444444444444444444444"}',
        ),
    )
    connection.execute(
        "INSERT INTO asset_events(event_digest, event_kind, episode_digest, event_json) "
        "VALUES(?, ?, ?, ?)",
        (
            "sha256:" + "6" * 64,
            "fixture.created",
            None,
            '{"kind":"fixture.created"}',
        ),
    )
    connection.execute(
        "INSERT INTO broker_artifacts(digest, content) VALUES(?, ?)",
        ("sha256:" + "7" * 64, b"fixture-artifact"),
    )
    connection.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", default=EXPECTED_COMMIT)
    args = parser.parse_args()
    version = importlib.metadata.version("adaptive-agent-runtime")
    if version != EXPECTED_VERSION:
        raise RuntimeError(f"expected adaptive-agent-runtime {EXPECTED_VERSION}, got {version}")
    if args.source_commit != EXPECTED_COMMIT:
        raise RuntimeError("source commit binding mismatch")

    from aar.runtime.reference_host import ReferenceHost

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "registry-v5.sqlite"
        host = ReferenceHost(
            database_path,
            now_ms=lambda: FIXED_NOW_MS,
            programmable_backend="plain",
            enable_durable_dispatch=False,
        )
        host.close()
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            populate_representative_rows(connection)
            tables = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            counts = {
                table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in tables
            }
            schema_versions = [
                int(row[0])
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_key_violation_count = len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            )
            if integrity != "ok" or foreign_key_violation_count != 0:
                raise RuntimeError("populated v5 fixture failed database integrity")
            row_set_digest = canonical_row_set_digest(connection, tables)
            data_row_set_digest = canonical_row_set_digest(
                connection,
                [table for table in tables if table != "schema_migrations"],
            )
            dump = (
                "PRAGMA foreign_keys=OFF;\n"
                + "\n".join(connection.iterdump())
                + "\nPRAGMA foreign_keys=ON;\n"
            )
        finally:
            connection.close()

    dump_path = args.output_dir / "registry-v5.sql"
    dump_bytes = dump.encode("utf-8")
    dump_path.write_bytes(dump_bytes)
    binding = {
        "schema_version": "aar.sdd-registry-v5-binding.v1",
        "package_version": version,
        "source_commit": args.source_commit,
        "fixed_now_ms": FIXED_NOW_MS,
        "schema_versions": schema_versions,
        "table_names": tables,
        "row_counts": counts,
        "populated_tables": sorted(
            table for table, count in counts.items() if count > 0
        ),
        "canonical_row_set_digest": row_set_digest,
        "canonical_data_row_set_digest": data_row_set_digest,
        "integrity_check": integrity,
        "foreign_key_violation_count": foreign_key_violation_count,
        "sql_dump_sha256": digest_bytes(dump_bytes),
        "sql_dump_size_bytes": len(dump_bytes),
    }
    (args.output_dir / "registry-v5-binding.json").write_text(
        json.dumps(binding, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(binding, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
