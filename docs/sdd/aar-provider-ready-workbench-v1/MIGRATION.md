# Migration and Host Activation

## 1. Scope

This release operationalizes the predecessor registry-v6 migration. It does not invent schema v7 by default and does not auto-migrate during ordinary `aar-supervisor` or `aar-mcp` startup.

The migration owner is an explicit operator command under exclusive runtime-home custody. A setup subprocess, MCP client, profile installer, or package installer is not cutover authority.

## 2. Why package upgrade is insufficient

Python package installation changes executable bytes, not the preserved SQLite registry. The v0.5 runtime can reopen a v5 registry because ordinary startup is compatibility-preserving and migration requires an exact snapshot, attestation, and authority transaction. Tool discovery can therefore show v8 while workbench admission remains unavailable. This is intentional fail-closed behavior; the successor supplies the missing operator journey rather than weakening startup.

## 3. External-to-DB authority

The required baseline authority is a local owner-controlled epoch directory; no new daemon or database is added.

```text
<runtime-home>/authority/
  runtime-owner.lock
  activations/<profile-id>/
    history/<activation-generation>-<authority-digest>.json
    current.json            # derived pointer; not history authority
  cutovers/<cutover-epoch>/
    prepared.json          # aar.operator-prepared-marker.v1, kind=cutover
    committed.json         # aar.operator-terminal-marker.v1 after verified DB/profile/history/current commit
    aborted.json           # terminal marker only before DB commit
    recovery-required.json # terminal marker preserving uncertainty
  restores/<restore-epoch>/
    prepared.json          # aar.operator-prepared-marker.v1, kind=restore
    committed.json         # aar.operator-terminal-marker.v1 after verified replacement
    recovery-required.json
```

Prepared/terminal markers and activation-history files are canonical, digest-bound, owner-only, immutable once published, and created by exclusive temp-write + file fsync + atomic rename + parent-directory fsync. Each per-profile history filename is content-addressed by its strict `aar.activation-generation-authority.v1` self digest. Fixed epoch filenames are integrity-protected by the strict inner/outer marker digests rather than pretending their full bytes equal a nested receipt digest. `current.json` is the one replaceable file, but it is only a derived exact-byte pointer to the highest valid immutable history record.

### Runtime-home lock and CAS protocol

The stable lock file is `<runtime-home>/authority/runtime-owner.lock`; inode replacement is forbidden. Ordinary supervisor startup acquires a shared cutover lock **and** the predecessor exclusive runtime-owner identity before opening the registry, then holds both for the supervisor lifetime. Every `aar-admin cutover apply|abort|reconcile|restore` and `aar-admin runtime initialize` mutation acquires the same file exclusively before final precheck and holds it through its named snapshot/DB/history/current/profile/terminal work. Therefore supervisor startup blocks/fails while an operator mutation owns the lock, and each mutation fails while any supervisor retains its shared lock.

A fresh cutover or restore epoch directory is created with exclusive-create semantics under the exclusive lock. Its `prepared.json` is the complete strict `aar.operator-prepared-marker.v1` CAS from `CONTRACTS.md §3`: the cutover variant embeds the validated plan, equal plan digest, verified snapshot and final DB/WAL/SHM observations; the restore variant embeds the exact cutover/snapshot/target/frontier inputs. Same epoch plus different bytes is conflict. A cutover marker binds `profile_id`, proposed `activation_generation`, exact observed immutable history tip or absence, and equal derived `current.json` digest or absence through its nested plan. Final precheck scans the complete history plus terminal markers, rejects a fork/missing member/stale pointer, and rejects any proposed generation less than or equal to the tip. Operator identity binds host boot identity, process start identity and operation input digest; raw PID is insufficient. If the process crashes, the OS releases the lock but markers/history remain. A replacement operator may inspect with matching status and mutate only through matching cutover `apply|abort|reconcile` or restore `reconcile`; supervisor startup and unrelated mutation remain refused until the prepared/DB/history/current/terminal tuple is classified. Lock loss before terminal marker is `CUTOVER_RECOVERY_REQUIRED`.

A host may later supply a revision/CAS authority adapter, but it must implement the same lock exclusion, prepared/committed/reconcile semantics and takeover rules. The local file authority is the required first product path.

## 4. Read-only plan

`aar-admin cutover plan` MUST:

1. resolve exact runtime-home/database identity;
2. prove no active supervisor/owner and no competing cutover lock holder;
3. open SQLite read-only/query-only and stat/hash existing DB/WAL/SHM state without executing a checkpoint or any filesystem/DB mutation;
4. run `PRAGMA integrity_check` and `foreign_key_check` read-only;
5. read schema migrations and issue a new plan only for the exact supported v5 schema; an already-v6 root returns existing status/receipt or recovery classification, never a new plan;
6. count/classify nonterminal operations, attempts, workbench jobs, caller tickets, and workers;
7. reject active/nonterminal state unless an explicit predecessor policy proves safe handling;
8. calculate canonical v5 row-set and migration SQL digests;
9. bind target source/wheel/activation-intent/skill/contract bytes and final-profile output path;
10. choose a fresh cutover epoch and snapshot destination;
11. emit exactly one canonical `aar.cutover-plan.v1` JSON document plus one trailing newline on stdout, with diagnostics on stderr and no runtime-home/DB/authority write.

Planning accepts no output path and does not stop a service, create a snapshot, prepare authority, migrate, or activate. An operator may redirect stdout to a file, but that is an external shell write and `apply --plan <file>` revalidates the exact untrusted bytes; `apply --plan -` consumes stdin directly.

### WAL/SHM evidence and restore rules

| Observed source state | Backup/apply behavior | Restore/disposition | Required evidence |
|---|---|---|---|
| supervisor or writer owns registry | reject; zero snapshot/marker/DB writes | none | owner identity and lock rejection |
| quiesced DB, no sidecars or zero-length sidecars | SQLite backup API to standalone snapshot; fsync and verify | sidecars remain absent; restore uses verified standalone snapshot | DB identity/hash, sidecar absence/size, snapshot hash, integrity/FK |
| quiesced WAL with committed frames | only after exclusive apply ownership, SQLite backup API MUST include committed WAL pages; raw DB/WAL copy is forbidden | snapshot is standalone authority; source WAL/SHM are not copied as restore inputs | read-only plan DB/WAL/SHM identities/sizes/hashes; apply pre/post sidecar observations; backup result; snapshot integrity/FK |
| WAL/SHM changes between plan and final precheck | reject stale plan before prepared marker | none | changed sidecar identity/digest |
| unreadable, hot, corrupt, or unclassifiable sidecar | `CUTOVER_RECOVERY_REQUIRED`; zero migration writes | operator recovery outside automatic path | failure classification and untouched DB/authority |
| pre-frontier restore authorized | under exclusive lock, open snapshot read-only, verify hash/integrity/FK; copy via SQLite backup API into fsynced temporary DB; atomically replace target | remove stale target `-wal`/`-shm` only after replacement is fsynced and while no handle exists; fsync parent; reopen read-only | plan/snapshot/target identity, replaced DB hash, sidecar disposition, post-restore integrity/FK |

SHM is coordination state, never durable business evidence. No sidecar is deleted merely because it exists; deletion is allowed only in the explicit verified restore row above. A failed backup or restore deletes only its temporary target and leaves source bytes and authority markers unchanged.

## 5. Apply transaction

`aar-admin cutover apply --plan -|<operator-materialized-file>` MUST perform this ordered transaction:

1. acquire the stable runtime-home cutover lock;
2. repeat every owner/database/nonterminal/integrity/candidate/intent check from the plan;
3. create a SQLite backup through the SQLite backup API and hash/read-verify it;
4. preserve and record WAL/SHM handling; do not copy a live database by raw file copy;
5. build the strict cutover `aar.operator-prepared-marker.v1` from the complete plan, verified snapshot and final-precheck DB/WAL/SHM observations, compute its self digest, and atomically publish it once;
6. begin one SQLite write transaction;
7. apply the reviewed `migration-v6.sql` bytes;
8. insert the exact migration-v6 attestation bound to snapshot, candidate, activation intent, contract, skill, SQL, `prepared_marker_digest`, proposed activation generation, and observed prior activation-authority digest through `intent_digest`; its legacy field `profile_digest` equals `intent_digest`;
9. run foreign-key and integrity checks required inside/before commit where supported;
10. commit once;
11. reopen read-only and verify schema v6, attestation, table/column/index inventory, canonical row preservation, and zero violations;
12. deterministically generate and atomically publish the final host profile from the exact intent plus migration-attestation digest; fsync file and parent;
13. scan/revalidate the immutable activation-history chain and committed markers; build the strict successor record linked to the exact tip, require a strictly larger generation, then exclusive-create/fsync/atomically publish its immutable history file plus parent fsync; history publication is the generation commit linearization point;
14. temp-write/fsync/atomically replace derived `current.json` with the exact history-record bytes plus parent fsync;
15. build the strict cutover receipt containing intent/final-profile/history-tip/current-pointer digests and output path, compute its self digest, then wrap that already-computed receipt in one strict `aar.operator-terminal-marker.v1`, compute the outer marker digest, and atomically publish `committed.json`;
16. release the lock.

The migration SQL and attestation share one SQLite commit. No partial DDL may become visible.

If step 3 completed but step 5 did not publish, a final snapshot is adoptable only when its path/size/digest and every plan/source/sidecar/candidate/intent byte still match under a new exclusive lock. An exact adopt proceeds to step 5. Mismatch or ambiguity is recovery-required and never overwrites/deletes the final snapshot. Only an unpublished operation-owned temporary file may be removed after proving no prepared marker references it.

## 6. Idempotency and reconcile

- Same plan/epoch/prepared-marker digest against already committed exact v6 plus exact immutable history tip, derived current pointer and valid terminal marker returns the nested existing receipt without a new DB or authority write.
- Same epoch with different plan/intent/candidate/database/prior-authority bytes is `CUTOVER_AUTHORITY_CONFLICT`.
- Exact v6 DB attestation with prepared but missing final profile/history record is changed only by the explicit matching reconcile command after all bytes and the still-current history-tip precondition match; it deterministically publishes the exact missing artifacts once.
- Exact immutable history tip with stale/absent current and a missing committed epoch marker is already generation-committed; reconcile validates the unbranched chain and DB/profile equality, repairs current to the exact tip, constructs the receipt from prepared/in-DB/history facts, and publishes the downstream terminal marker. It never appends another generation.
- A different/forked/missing history record, current pointing below/not on the tip, equal/lower generation request, broken previous-digest link, marker disagreement, or ABA observation is `ACTIVATION_HISTORY_CONFLICT`/`CUTOVER_RECOVERY_REQUIRED`, never overwrite authority.
- Prepared marker with unchanged v5 DB returns `ABORT_OR_APPLY_REQUIRED` from reconcile with zero write; only explicit matching `cutover apply` or `cutover abort` mutates it.
- Ambiguous DB state, mismatching attestation, corrupt snapshot, owner drift, or unclassifiable WAL state is `CUTOVER_RECOVERY_REQUIRED`; no automatic retry.

## 7. Rollback frontier

The safest rule is forward-only after the v6 SQLite commit.

`restore` is allowed only when all of the following are proven under the cutover lock:

- no committed v6 attestation exists;
- no v6 runtime generation, immutable activation-history record, or committed epoch marker was published;
- no v6 workbench/caller/activation row or operation was admitted;
- snapshot identity/hash and intended target database match;
- operator explicitly names the plan and snapshot.

Otherwise rollback means restoring service from independently accepted backup/cutover procedure, not an automatic down migration. Semantic down-migration and deletion of v6 evidence are forbidden.

An allowed restore uses a new `restore_epoch`. Under exclusive lock it first revalidates the cutover plan/prepared marker, snapshot, target identity/sidecars and all three frontier booleans. It also requires the original cutover epoch to have exactly one outer terminal marker: `cutover_aborted` when no DB replacement occurred, or `cutover_recovery_required` when the pre-frontier replacement is the condition being repaired; a committed cutover is never restorable. The restore prepared marker binds that exact outer marker digest before target replacement. Only afterward may it copy the verified standalone snapshot through SQLite `Connection.backup()` into a fsynced temporary database and atomically replace the target. After reopened integrity/FK/sidecar verification it builds `aar.restore-receipt.v1` with both cutover/restore prepared-marker digests and the required original `cutover_terminal_marker_digest`, computes the receipt self digest, wraps it in one downstream restore `aar.operator-terminal-marker.v1`, and publishes `committed.json`. The exact `cutover_recovery_required` → `restore_committed` digest pair terminally resolves the original cutover for startup scanning without rewriting either epoch. A crash before target replacement may be explicitly reconciled or retried against the same restore marker; a crash after replacement reconstructs only the exact receipt/terminal marker from the prepared marker and verified target. Failure before replacement leaves no success terminal marker; ambiguous replacement publishes/retains only recovery-required and never repeats replacement blindly.

## 8. Activation verify and start

After cutover and before supervisor start:

```text
aar-admin activation verify --runtime-home <...> --profile <...>
```

verifies candidate/profile/migration/adapter/grant/route/recovery compatibility, the complete append-only activation history and committed markers, and exact equality of derived `current.json` with the history tip, without provider calls or writes.

Supervisor adds one explicit argument:

```text
aar-supervisor ... --activation-profile /owner-controlled/path/profile.json
```

The path is operator-owned and not job-controlled. Startup first acquires and retains the shared cutover lock plus predecessor runtime-owner identity; it refuses any unmatched prepared/recovery marker. It fails before Ready publication if verification differs, if the authority record is absent/stale, or if its profile/intent/attestation/generation fields do not match. The supervisor constructs package-registered factories/adapters, derives capabilities, configures the bounded grant issuer, then atomically emits activation readback and one generation-unique Ready/discovery record that bind `authority_digest` as the final startup step. A client/session refresh may still be required for catalog pickup; standalone discovery is not substituted for live-host readback.

Ordinary `aar-mcp` remains an attach client over the durable supervisor. Embedded reference mode remains reference/non-durable and cannot satisfy production activation.

## 9. Empty-runtime initialization

`aar-admin runtime initialize --runtime-home ...` is an explicit **canonical empty-v5 bootstrap** mutator and not a direct-v6 or cutover authority. Under the same exclusive runtime-home lock it accepts only:

- an absent registry file;
- an exact uninitialized SQLite file with no `schema_migrations`, user tables, WAL/SHM state or domain rows; or
- a canonical empty v5 registry with zero domain/nonterminal rows, no v6 attestation and no activation history.

For absent/uninitialized input it invokes only the existing `Registry` initialization transaction, which creates the normal contiguous v1–v5 schema/migration rows. It creates no initialization directory, cutover plan, snapshot, marker, epoch, receipt, final profile, activation-history record, current pointer, or provider work. It then verifies the resulting canonical empty-v5 bytes and returns ordinary activation status `migration_required`. Against an already canonical empty-v5 registry it is a read-only idempotent classification.

The operator must next run the ordinary read-only `cutover plan --runtime-home ... --intent ... --profile-output ...`, retain one exact emitted `aar.cutover-plan.v1`, and pass those bytes to ordinary `cutover apply`. A lost plan before apply has caused no effect and may be replaced. Once apply creates a snapshot/prepared marker, restart/reconcile authority comes only from that exact plan and immutable marker; initialize never reconstructs or adopts an epoch under a new process identity or timestamp.

Caught exceptions/fault injections before the v5 transaction commits roll back to absent/uninitialized input and may be retried; caught faults after commit leave the allowed canonical empty-v5 input. **Abrupt process loss is not claimed crash-convergent in v1**: because the frozen Registry enables WAL before the v1–v5 transaction, a killed process may leave an uncommitted database plus WAL/SHM residue. `runtime initialize` must classify any such residue as `UNINITIALIZED_RUNTIME_RESIDUE` and fail closed without deleting, checkpointing, adopting, or retrying it. Only an explicit operator decision outside this command may discard a proven disposable root and start again. Arbitrary/committed WAL or SHM bytes and every non-empty or non-canonical v5 registry remain refused. There is no direct-fresh-v6 path, separate fresh receipt/attestation/epoch authority, fabricated v5 row set, automatic sidecar recovery, or schema-v7 requirement. The later ordinary committed cutover receipt and v6 attestation still bind a real snapshot and canonical empty-v5 row-set digest. Ready remains refused until that ordinary v6 attestation, final profile, append-only history, derived current pointer and committed cutover marker agree.

## 10. Local canonical lineage prerequisite

Current specification bytes live in a dirty local branch whose tracked product source predates v0.5. Before implementation:

1. record current dirty status and preserve all user work;
2. fetch only with user authority;
3. create a clean worktree from exact accepted base `bd30df40...` or a later explicitly reviewed successor;
4. import this SDD unchanged or record a reviewed amendment;
5. verify the baseline 743-test source suite or the accepted equivalent on exact bytes;
6. run a source-delta review before writing RED tests.

No reset, clean, rebase, forced checkout, or history rewrite is authorized by this SDD.

## 11. Required negative cases

- active supervisor or replacement owner;
- stale/reused raw PID with mismatching process identity;
- plan/database/intent/candidate/WAL drift;
- nonterminal operation/ticket/worker;
- failed integrity/foreign-key check;
- raw copied/incomplete snapshot;
- prepared marker conflict;
- crash before/after each SQLite commit and marker publication;
- attestation inserted without all v6 objects;
- v6 objects without attestation;
- repeated exact apply;
- absent/history-tip generation first commit, equal-generation reuse, lower generation, higher-generation successor and changed previous-tip digest;
- concurrent preserved-v5 cutover/empty-runtime-initialize intents for the same profile, equal-value ABA, fork/missing/deleted history, stale or rolled-back current pointer, crash before/after immutable history publication and derived-current replacement, and missing terminal marker after generation commit;
- absent/uninitialized/canonical-empty-v5 caught-fault rollback, abrupt process-loss WAL/SHM residue refusal, real snapshot/row-set binding, and absence of automatic sidecar recovery or separate fresh-v6 authority;
- exact versus changed-input cutover/restore reconcile and explicit pre-DB cutover abort/apply choice;
- read-only plan against absent/present WAL/SHM with zero checkpoint/filesystem/DB mutation canaries;
- changed bytes under an existing idempotency/epoch identity;
- restore after v6 Ready or durable v6 write;
- activation intent or final profile containing credentials, shell expansion, arbitrary endpoint, unknown field, or cyclic/mismatching digest;
- adapter factory/manifest mismatch;
- start with v5 DB, stale intent/final profile, stale route catalog, stale grant policy, or stale recovery digest.

Each case checks both result classification and absence of unauthorized DB/profile/service mutation.
