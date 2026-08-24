# Database preparation — clean-install rev2

## 1. Boundary

`0.6.0a0` performs no operational transition of an existing runtime. The only preparation is:

```text
absent target -> staged canonical empty v1-v5 -> unchanged v6 DDL + attestation
-> staged profile/history/current and generation-independent evidence -> one publication
```

The frozen `docs/sdd/aar-rlm-native-workbench-v2/migration-v6.sql` is executed only against the newly created staged empty-v5 database. No schema v7 is introduced; this contract has no schema v7. An existing file, directory, symlink, database, WAL/SHM tree, authority/history/current tree, or unknown target entry returns `FRESH_INSTALL_TARGET_EXISTS` before mutation. The product never opens an old root for mutation.

The exact sole public mutation is:

```text
aar-admin runtime install --runtime-home <absolute-runtime-home> --intent <exact-intent> --candidate-receipt <absolute-json> --wheel <absolute-wheel>
```

Status and activation verification are read-only. No aar-admin cutover command exists, and no v5 initializer or transition mutator is reachable.

## 2. Exact target preflight

The installer requires Linux/WSL v0.6 support conditions: absolute target, no NUL, absent final component, and every existing ancestor including the parent a UTF-8-encodable non-symlink directory. The existing parent is resolved strictly. `canonical_target` is that resolved parent joined with the original final component; no Unicode normalization or case folding occurs. Canonical JSON is UTF-8 with POSIX `/` separators.

The exact runtime-home object is:

```json
{"database":"<canonical_target>/reference.sqlite3","runtime_home":"<canonical_target>"}
```

Its canonical SHA-256 is `runtime_home_digest`. The exact database identity is `db-` plus the lowercase hex part of:

```json
{"schema_version":"aar.clean-install-database-identity.v1","runtime_home_digest":"<runtime_home_digest>","database_name":"reference.sqlite3"}
```

The issuer, installer, staging verifier, and startup verifier use the same algorithm. Raw/unresolved path, inode/dev, and staging path are not semantic identity. Native Windows fails with `FRESH_INSTALL_PUBLICATION_UNSUPPORTED` before publication.

Clean-install preflight requires the exact supplied intent to carry `activation_generation=1` and `previous_activation_authority_digest=null`. Any other self-digested intent fails `FRESH_INSTALL_INITIAL_AUTHORITY_INVALID` before staging and is never rewritten.

## 3. Handles and staging

The installer opens and retains no-follow handles for the resolved parent and the required ancestor identity chain and records fstat dev/ino/mode/owner. It creates one fresh sibling by parent-dirfd-relative exclusive creation at mode `0700`, retains the staging fd/dev/ino, and performs all staging operations relative and no-follow. It never scans or adopts a stale sibling.

Before and after publication, the target-to-parent/ancestor identity chain must match the retained chain. Cleanup compares identity using handles; parent, ancestor, or stage drift refuses deletion and leaves inert manual evidence. A string path is never enough authority for recursive deletion.

The Linux `SQLiteStageAdapter` is owned by the standalone installer. Before database mutation it verifies `/proc/self/fd/<stage_fd>` resolves to the retained stage dev/ino, then gives existing registry/backup/v6 helpers only fixed child paths below that fd root. It fences stage identity before/after each helper and validates database, backup, WAL, and SHM entries no-follow. Adapter absence is unsupported before mutation; parent/stage name replacement cannot redirect SQLite work and prevents later publication.

Publication is:

```text
renameat2(parent_fd, stage_name, parent_fd, target_name, RENAME_NOREPLACE)
```

`EEXIST` is refusal, the target inode must equal the staged inode on success, and the parent fd is fsynced. Parent/ancestor replacement returns `FRESH_INSTALL_PARENT_REPLACED` and never success. Deterministic barriers, not sleeps, drive the parent/stage substitution cells.

## 4. Empty v5 and v6 transaction

Within staging, the existing `OperationRegistry` transaction constructs contiguous canonical empty v1-v5 and verifies zero operations, tickets, workers, domain/nonterminal rows, integrity `ok`, zero foreign-key violations, and no unknown residue.

Before v6, the staged database is copied with the approved SQLite backup API to one temporary staging backup. It is closed, fsynced, reopened read-only, checked for integrity/FK and canonical empty-v5 rows, and hashed. The verified byte size is positive. The backup remains available through final staged verification and is removed before final fsync/publication; startup never claims to reread it.

The exact clean-preparation object is the one in `CONTRACTS.md`, with exactly these fields and no staging path:

```text
schema_version, install_epoch, runtime_home_digest, database_identity,
database_name, empty_v5_backup_digest, empty_v5_backup_size_bytes,
canonical_v5_row_set_digest, intent_digest, candidate{source_commit,
wheel_digest, contract_manifest_digest, skill_digest}, migration_sql_digest,
projected_external_authority_store_id
```

`external_authority_prepared_digest` is its canonical digest. The intent authority is projected to legal `authority-<64hex>`, and the backup identity to legal `empty-v5-<64hex>` using the exact `canonical_sha256(...).removeprefix("sha256:")` formulas in `CONTRACTS.md`.

`install_epoch` is issued once after preflight and before staging as `"install-" + secrets.token_hex(32)`. It is immutable within that CLI invocation, equals attestation `cutover_epoch`, and is never reused by a new invocation or used to adopt stale staging.

The unchanged v6 DDL and complete `MigrationAttestationPayload` commit in one SQLite transaction. `cutover_epoch` is `install_epoch`; `snapshot_id` is the legal deterministic backup token; `profile_digest` is the exact intent digest; timestamps are exact integer milliseconds with started <= completed; and `attestation_digest` hashes the complete payload. The matrix has a separate cell for before-first, before attestation insert, before schema-migration insert, each exact `fail_after_statement_01` through `fail_after_statement_25`, before commit, and after-commit readback. A fault cannot publish partial v6.

The 25 frozen statement names, in order, are:

```text
01 migration_v6_attestations
02 rlm_workbench_jobs
03 rlm_workbench_cells
04 rlm_workbench_suspensions
05 caller_work_tickets
06 caller_work_candidate_receipts
07 caller_work_command_receipts
08 caller_work_command_receipts_no_update
09 caller_work_command_receipts_no_delete
10 rlm_workbench_successor_outbox
11 rlm_workbench_attempt_authority
12 rlm_workbench_rebind_transfers
13 rlm_workbench_artifact_stages
14 rlm_workbench_cell_manifests
15 rlm_workbench_finalization_manifests
16 broker_contract_catalog_v2
17 broker_backend_availability_v2
18 idx_workbench_phase_deadline
19 idx_workbench_cells_state
20 idx_caller_work_state_deadline
21 idx_caller_work_claim_expiry
22 idx_candidate_receipts_ticket
23 idx_caller_command_receipts_ticket
24 idx_artifact_stages_state
25 idx_successor_outbox_state
```

## 5. Profile, receipt, and publication

After v6 read-only verification, the installer builds the final profile, generation-1 immutable authority with `previous_activation_authority_digest=null`, and an exact `current.json` byte copy. C1 verifies the exact planned candidate receipt, wheel, fixed assets, factory declarations, route policy, and grant policy. It does not build executable capability rows, `WorkbenchGrantSet`, session grants, or runtime health because no `runtime_generation` exists before publication.

The exact validated credential-free receipt is copied to `authority/install-candidate-receipt.json`. The staged tree is completely verified and fsynced, then published once. After publication, C2 verifies immutable install/profile/evidence, allocates/fences the normal runtime generation, and invokes the standalone activation coordinator. Its store publishes and reads back the exact generation-bound grant set before Ready. Startup never initializes, migrates, repairs, adopts, or rewrites install state.

## 6. Conditional old-root boundary

If a legacy root exists and the operator intends to remove it, the operator must independently prepare and read back one inactive verified external archive before removal. On a genuinely fresh host, the acceptance alternative is a proven no-old-root observation. A nonexistent archive is not mandatory. The product never mutates, adopts, downgrades, restores, or repairs an old root.

## 7. Non-claims

This is a specification-only database preparation contract. It does not claim implementation, an actual transaction, a wheel, an install, startup, Ready, or provider work.
