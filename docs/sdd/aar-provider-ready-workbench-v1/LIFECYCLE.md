# Lifecycle and temporal ownership — clean-install rev2

## 1. Phases and safety

```text
ABSENT_TARGET -> PRECHECKED -> STAGING -> STAGED_V5 -> STAGED_V6_VERIFIED
  -> PROFILE_HISTORY_VERIFIED -> PUBLISHING -> PUBLISHED -> starting -> Ready
```

The first seven observations are invocation-local. `PUBLISHED` exists only after the no-replace operation succeeds. Runtime `starting` and Ready belong to the supervisor after publication. There is no durable install recovery state and no automatic stale-stage adoption.

Safety requires: absent target; no symlink traversal; exact target identity; retained parent/ancestor handles; one mode-0700 stage; atomic v5/v6 transaction; complete staged evidence; no-follow cleanup; one no-replace publication; parent fsync; and post-publication immutable readback. Liveness requires a writable Linux/WSL parent, a constructible same-filesystem primitive, bounded SQLite progress, and supervisor fairness. Provider progress is not required for install safety.

## 2. Target and temporal ownership

The target is absolute and NUL-free. Its final component must be absent. Every existing ancestor, including parent, is a UTF-8-encodable non-symlink directory. The existing parent is strictly resolved; `canonical_target` joins that parent with the original final component. There is no Unicode normalization or case folding. Native Windows returns `FRESH_INSTALL_PUBLICATION_UNSUPPORTED` in v0.6.

The supplied intent must be initial authority: `activation_generation=1` and `previous_activation_authority_digest=null`. After all descriptor/target preflights pass, the installer issues one `^install-[0-9a-f]{64}$` invocation epoch and freezes it through preparation, snapshot, attestation `cutover_epoch`, and stage evidence. A new CLI invocation always issues a new token and never adopts stale staging.

The installer opens and retains no-follow handles for the resolved parent and each necessary ancestor identity chain. For every handle it records fstat dev/ino/mode/owner. Staging is created with an exclusive parent-dirfd-relative operation at mode `0700`; its fd/dev/ino are retained.

All stage creation, file opening, database work, verification, cleanup, and publication is relative to retained handles and no-follow. A cleanup operation compares the retained identity and current fstat identity. Drift returns contained/manual state and leaves inert evidence; it does not recursively delete a path selected only by string. Parent and ancestor path identity chains are checked immediately before and after publication.

For SQLite pathname APIs, the installer-owned Linux `SQLiteStageAdapter` first proves `/proc/self/fd/<stage_fd>` resolves to the retained stage dev/ino, then passes only fixed children under that fd root to the unchanged registry/backup/v6 helpers. Stage fstat and database/backup/WAL/SHM no-follow checks run before and after each helper. Adapter absence fails unsupported before mutation; a renamed/replaced stage entry cannot redirect the retained-fd operation and blocks later publication/cleanup.

The only publication operation is:

```text
renameat2(parent_fd, stage_name, parent_fd, target_name, RENAME_NOREPLACE)
```

`EEXIST` is target refusal (`FRESH_INSTALL_TARGET_RACE` when caused by the publication race). A successful readback requires target inode == staged inode and fsync of the publication parent fd. Parent or ancestor replacement is `FRESH_INSTALL_PARENT_REPLACED`; it is never success. Deterministic barriers drive substitution tests; sleeps are forbidden. Same-principal kernel/handle attacks beyond these tests are outside the `trusted_local` threat model.

## 3. Lifecycle order

1. **ABSENT_TARGET/PRECHECKED:** validate exact intent, receipt, wheel, route, grant, target algorithm, database identity, and platform. Existing target types return `FRESH_INSTALL_TARGET_EXISTS` before staging.
2. **STAGING:** create one fresh invocation-owned sibling by retained parent fd. A stale sibling is inert and never adopted.
3. **STAGED_V5:** use the existing `OperationRegistry` transaction to create canonical empty v1-v5 and verify contiguous rows, zero domain rows, integrity/FK, and no residue.
4. **STAGED_V6_VERIFIED:** verify the positive-size empty-v5 backup, execute unchanged migration-v6.sql plus the exact attestation in one SQLite transaction, reopen read-only, and verify all 25 DDL statement outputs, attestation fields, digests, integrity/FK, and empty domain.
5. **PROFILE_HISTORY_VERIFIED:** C1 builds the final profile, generation-1 immutable history (`previous_activation_authority_digest=null`), exact current pointer, and generation-independent receipt/asset/factory/route/grant-policy evidence. Copy the receipt to `authority/install-candidate-receipt.json`.
6. **PUBLISHING:** verify all files, directories, modes, fds, digests, and fsyncs; recheck identity chains; publish once with renameat2.
7. **PUBLISHED/starting/Ready:** C2 revalidates immutable install/profile/evidence, allocates/fences the normal runtime generation, invokes the standalone activation coordinator, atomically publishes/reads back `authority/runtime-generations/<runtime_generation:020d>/workbench-grant-set.json`, and publishes Ready only after readback. Session grants are current-generation memory-only and require an explicit policy-approved request.

The installer never constructs executable capability rows, WorkbenchGrantSet, session grants, or runtime health before publication because runtime_generation does not exist. Startup may perform normal generation/grant writes but never initializes, migrates, repairs, adopts, or rewrites schema/profile/install state.

## 4. v6 transaction and backup boundaries

The temporary empty-v5 backup is created from the staged database, closed, fsynced, reopened read-only, checked for integrity/FK and canonical empty rows, and hashed. It has positive verified size. It remains available through final staged verification and is removed before final fsync/publication. Startup verifies attestation/profile/inventory and does not claim to reread the deleted backup.

The unchanged v6 DDL and exact attestation commit in one transaction. The acceptance matrix has separate cells for before-first, before attestation insert, before schema-migration insert, each `fail_after_statement_01` through `fail_after_statement_25`, before commit, and after-commit readback. Every caught fault is contained below staging and cannot expose partial v6.

## 5. Cleanup and crash semantics

- Refusal before stage creation is zero-write.
- A caught pre-publication fault deletes only the identity-matched current stage.
- Parent, ancestor, or stage substitution during verification or cleanup refuses deletion and leaves inert evidence for manual handling.
- Abrupt process loss may leave a sibling; the next invocation uses fresh staging and never scans or adopts residue.
- Target appearance at publication returns EEXIST/refusal and leaves the existing target byte-identical.
- A successful publication is not repeated; a rerun returns `FRESH_INSTALL_TARGET_EXISTS`.
- Startup verification failure after publication is read-only and never repairs, migrates, adopts, or mutates an old root.

## 6. D1/D2 and admission retention

Caller-delegated planning keeps exact `{kind, phase, step_index}` model.request ownership, released waiting leases, `mark_send_started` before physical dispatch, claim/send-start, cell-free successor CAS, and one-time consume. `settled_success`, `settled_failure`, and `cancelled_certain` use their frozen rules; `cancelled_before_send`, `cancel_requested`, `outcome_unknown`, and `quarantined` remain no-send/reconcile-only as applicable. Possibly-sent work never blindly resends. The planner has no hidden cell-bound authority. `GRANT_DENIED` remains authority failure; only current same-generation health loss is `CAPABILITY_UNAVAILABLE`.

## 7. Old-root boundary

Old-root acceptance is conditional and has separate cells. If a legacy root exists and the operator intends removal, the operator must provide one inactive verified archive/readback before removal. On a genuinely fresh host, acceptance instead proves a no-old-root observation. A nonexistent archive is not required. The product never opens an old root for mutation, adoption, downgrade, restore, or repair, and old/new roots cannot share v0.6 authority.
