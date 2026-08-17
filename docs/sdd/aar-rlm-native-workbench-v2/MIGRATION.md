# AR-RW Additive Registry Storage Schema v6

**Normative SQL:** `migration-v6.sql`

## 1. Baseline and scope

AR-RW targets the released `a585ac52...` baseline. SQLite registry storage migration version 6 is additive. It does not alter contract-level `aar.runtime.v1` or `aar.operation-continuity.v1` bytes.

- frozen `operations.state` values;
- v1 RLM tables/rows;
- operation/attempt/lease identities;
- public-plugin databases;
- v1 broker contract rows.

New workbench phase, caller-work, artifact staging and finalization truth is held in dedicated tables. Existing operations remain readable with no workbench row.

## 2. Migration identity

The v6 database attestation row and the external cutover authority MUST bind:

- migration version `6`;
- SHA-256 of the exact `migration-v6.sql` bytes;
- source commit and exact wheel digest applying it;
- cutover epoch and immutable snapshot ID;
- prior SQLite-backup digest, size and canonical v5 row-set digest;
- start/end timestamps, `foreign_key_check` result and `integrity_check` result;
- candidate schema/capability readback digest;
- external cutover-authority ledger location and anchored record digest.

Do not compute the migration digest from version number alone.

The cutover authority is a separate append-only, fsynced store outside both the live database and its rollback snapshot. Every service launcher MUST consult it before opening either database. Its only legal terminal paths are `prepared -> candidate_active -> rolled_back_before_write` or `prepared -> candidate_active -> post_snapshot_write -> retired_forward`. `post_snapshot_write` is conservative: it is durably appended before the candidate can admit or mutate work or initiate an external effect. The v6 database stores the corresponding prepared-record digest, but the independently durable authority is what prevents an old executable from silently reopening a stale snapshot.

## 3. Application sequence

1. Stop new admissions, fence all writers and external dispatch, and wait for the runtime resource owner to quiesce.
2. Create the rollback snapshot with SQLite's online backup API while the fence is held. A raw copy of `registry.sqlite` is forbidden. An alternative requires every connection closed, `wal_checkpoint(TRUNCATE)` success, proof that no WAL-dependent pages remain, then copying the database.
3. Open the snapshot independently; require zero `foreign_key_check` rows, `integrity_check=ok`, the expected migration set and exact canonical v5 row-set digest. Fsync the snapshot and containing directory.
4. Append and fsync the external `prepared` cutover-authority record containing cutover epoch, snapshot identity/digest and source/package/profile/skill/contract identities.
5. Open one live-database migration connection with WAL, foreign keys and `synchronous=FULL`; execute `BEGIN IMMEDIATE`.
6. Verify the current migration set, baseline tables, source snapshot identity and authority-record digest.
7. Execute `migration-v6.sql`.
8. Insert both the migration registry row and `migration_v6_attestations` row in the same transaction.
9. Run `PRAGMA foreign_key_check` and `PRAGMA integrity_check`; update the attestation with exact results and end timestamp.
10. Commit, fsync the live database and containing directory, and append/fsync `candidate_active`.
11. Reopen with the exact candidate and run schema/capability readback before admission.
12. Immediately before enabling the first candidate admission, mutation or external dispatch, CAS-append and fsync `post_snapshot_write`; only then release the admission/write fence.

Failure before step 12 may restore the prior snapshot after verifying the authority is still `prepared` or `candidate_active` and no post-snapshot mutation exists. Once `post_snapshot_write` is durable, snapshot rollback is forbidden; recovery proceeds forward on the v6 database. Restoring an older executable after that point requires a separately specified compatibility journal and exhaustive reconciliation of every post-snapshot mutation and may-have-sent effect before admissions resume; AR-RW v1 does not define such a journal.

## 4. Repository concurrency policy

All new DB-backed broker/workbench repositories use a connection factory with one short-lived SQLite connection per transaction:

- WAL enabled;
- foreign keys enabled;
- 5,000 ms busy timeout;
- bounded retry only for `SQLITE_BUSY` before cumulative deadline;
- `BEGIN IMMEDIATE` for writes;
- no connection shared across threads;
- close fences new transactions and waits for active transactions.

A shared `check_same_thread=False` implementation is not an alternative for AR-RW v1.

## 5. Store authorities

- `rlm_workbench_jobs`: workbench control/phase/result projection.
- `rlm_workbench_cells`: cell identity, fences, checkpoints and commit state.
- `rlm_workbench_suspensions`: durable suspension and caller-ticket binding. The suspension and ticket are joined by a composite foreign key over operation/revision, ticket ID, method, contract ID, request digest and canonical logical-owner JSON; matching only operation/revision is insufficient.
- `caller_work_tickets`: authoritative caller-work automaton. `send_started_at_unix_ms` is the conservative pre-dispatch linearization mark; `sent_request_digest` is mandatory from `send_started` onward; `sent_at_unix_ms` and `provider_or_child_request_id` remain nullable unless the host actually observes them. `pending` requires every claimant, adapter, physical-attempt, sent-evidence and settlement column to be null; every non-pending state requires the complete claimant/adapter/attempt identity. `send_reserved` requires all four sent-evidence columns to be null. Nonterminal and quarantined states require settlement columns to be null; `settled_success` requires observed `sent_at_unix_ms`; and `cancelled_certain` requires the physical attempt, durable send mark and settlement receipt.
- `caller_work_candidate_receipts`: immutable late/current evidence lane.
- `rlm_workbench_successor_outbox`: exactly-one successor admission.
- `rlm_workbench_artifact_stages`: invisible staged bytes and binding intent.
- `rlm_workbench_cell_manifests`: certain cell-commit authority.
- `rlm_workbench_finalization_manifests`: single terminal output/artifact commit point.
- `broker_contract_catalog_v2`: immutable version-qualified method/schema truth.
- `broker_backend_availability_v2`: runtime adapter/configuration truth with a catalog foreign key.

No table may independently publish terminal result or final artifacts. `FinalizationManifest` is the logical commit authority.

## 6. Compatibility and rollback

Older executables may reject a database carrying migration v6 even though tables are additive. The preserved pre-v6 snapshot may therefore be reopened with the preserved executable **only** when the external cutover authority can CAS `candidate_active -> rolled_back_before_write` and proves that `post_snapshot_write` was never appended. No destructive down-migration is required or allowed on this path.

After `post_snapshot_write`, the pre-v6 snapshot is permanently stale. Candidate failure retains the v6 database as authority and evidence, stops new admission/dispatch, and performs forward recovery and reconciliation on v6. Neither an operator nor an old launcher may select the prior snapshot. V2 rows are never dropped or rewritten as v1 success. AR-RW v1 deliberately provides no post-write downgrade journal; adding one requires a new reviewed migration contract and exhaustive external-effect reconciliation.

## 7. Required migration tests

- regenerate `fixtures/registry-v5.sql` with exact package `0.4.0a6` and source `a585ac52...`;
- bind `fixtures/registry-v5-binding.json` and its SQL SHA-256;
- require deterministic operations, attempts, leases, RLM, workspace and asset rows in the v5 fixture;
- create a committed row that remains in WAL and prove SQLite backup API includes it;
- migrate the populated v5 snapshot while preserving the canonical non-migration row-set digest;
- migration crash before/after DDL and registry insertion;
- migration DDL, schema registry row and `migration_v6_attestations` row commit atomically;
- duplicate migration replay;
- wrong SQL digest;
- missing/extra baseline table;
- foreign-key and integrity failures are checked before invoking the old executable;
- the migrated database is rejected specifically with `UnsupportedRegistrySchema` stating schema 6 is newer than supported schema 5; arbitrary import, configuration, corruption or I/O failures do not satisfy this gate;
- preserved v5 snapshot reopen succeeds, leaves every stable data table byte-equivalent, and changes `runtime_meta` only by the exact expected startup transition `generation = generation + 1`;
- candidate reopen/readback;
- valid `rolled_back_before_write` cutover path;
- valid `post_snapshot_write -> retired_forward` path;
- explicit rejection of rollback after `post_snapshot_write`;
- concurrent admission blocked during migration.

The current SDD receipt is `fixtures/registry-v5-migration-verification.json`. It is generated by `verify_registry_v5_migration.py` under the exact v0.4.0a6 environment. It binds the baseline SQL and binding files, contract manifest, verifier bytes, VCS commit/tag, an actual installed-distribution file inventory digest, and the pre/post frozen v5 row-set digests. It is invalidated whenever any bound input or executable verifier byte changes—not only when `migration-v6.sql` changes.
