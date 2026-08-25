# A1b-op1 — Operator Authority Strict Models

**Status:** `packet_review_pending`

**Parent lane:** A1b — operator/readback/evaluation contract models

**Task-start candidate parent:** `8fe0a64bafd7e3612dfc11854c8f43be07801cef`

**Frozen SDD binding:** Generation-10 semantic tree `sha256:cf6874ee870a1e2bcca3fb83b785213ea8b2f0205e4a54afaf9161b609925ab1`; complete SDD tree `sha256:fb4dbd69802191f411c6df9281b57e8ca45085012179ff56c403ca940dfbef3a`; receipt `sha256:bd63c52ce6eb88bc6ceac7a5adb1d80f546bfbe2cd7eae08728b39e5ca4154f2`.

**A1a dependency:** focused-green product commit `3ca321fd0df5a20a969965a0c1705fdd02dc83b6`; evidence-control parent `8fe0a64bafd7e3612dfc11854c8f43be07801cef`.

## 1. Outcome

Implement the strict, transport-neutral, inert data-model layer for the first six remaining operator authority schemas and their nested records:

1. `aar.activation-generation-authority.v1`;
2. `aar.cutover-plan.v1`;
3. `aar.operator-prepared-marker.v1`;
4. `aar.cutover-receipt.v1`;
5. `aar.restore-receipt.v1`;
6. `aar.operator-terminal-marker.v1`.

This slice proves strict shape, lexical/bound/nullability rules, acyclic nested/self digests and document-local cross-field invariants only. It does not plan, snapshot, lock, migrate, restore, reconcile, write a DB/file, publish markers/history/current, start/stop a service, allocate an epoch, or close any acceptance row by itself.

## 2. Exclusive writable paths

Exactly:

- `src/aar/provider_ready_operator_models.py`
- `tests/test_provider_ready_operator_models.py`

Both paths must be absent and collision-free at task start. No other path may be edited, staged or committed.

## 3. Frozen dependencies and reuse

Reuse package-owned strict seams rather than cloning scalar rules:

- `StrictModel`, `Digest`, `OpaqueToken`, `PositiveCounter`, `BudgetCounter` and other exact predecessor scalars from `aar.schemas`;
- `canonical_sha256` from `aar.canonical`;
- `ProviderReadyCandidate`, `PackageVersion`, `SourceCommit`, `LocalAuthorityStoreId` from `aar.provider_ready_models` where the wire domain is identical.

`CandidateBinding` may be a strict no-extra subclass or an exact alias/projection of `ProviderReadyCandidate`, but its emitted wire properties and validation must be byte-for-byte equivalent and must not introduce defaults. Do not change A1a source/tests.

No arbitrary paths are opened or normalized. `OperatorPath` is inert strict UTF-8 data, `1..4096` code points with no NUL. No model or builder may touch the filesystem, SQLite, environment, subprocess, socket, network, credentials, runtime or clock.

## 4. Nested strict records

Implement strict required-only records with `additionalProperties=false`, no defaults and exact domains:

- `FileArtifact`: `artifact_id: OpaqueToken`, `digest: Digest`, `size_bytes: 0..9223372036854775807`.
- `SidecarObservation`: `state: absent|present`, nullable `file_identity`, `size_bytes`, `digest`; absent requires all null, present requires all non-null.
- `DatabaseChecks`: `integrity_result=ok`, `foreign_key_violation_count=0`.
- `CandidateBinding`: exact A1a candidate fields/domain.
- `EpochOwnerBinding`: `authority_store_id`, `operator_identity_digest`, `runtime_owner_state=absent`, `exclusive_lock_state=available`.
- `NonterminalCounts`: nonnegative integer fields `operations`, `attempts`, `workbench_jobs`, `caller_tickets`, `workers`; every value must be zero for a valid issued plan.
- `SnapshotPlan`: `snapshot_id`, inert `destination: OperatorPath`, `backup_mode=sqlite_backup`.
- `CutoverPreparation`: complete `plan`, equal `plan_digest`, verified `snapshot`, `pre_migration_database_digest`, final-precheck `wal` and `shm`; snapshot ID equals `plan.snapshot.snapshot_id`, pre-migration DB digest equals the plan source DB digest, and sidecar observations equal the plan.
- `RestoreFrontierProof`: exactly three required literal-true booleans `no_committed_v6_attestation`, `no_activation_history_record`, `no_v6_runtime_or_workbench_write`.
- `RestorePreparation`: `cutover_plan_digest`, `cutover_prepared_marker_digest`, non-null `cutover_terminal_marker_digest`, verified `snapshot`, `runtime_home_digest`, `target_database_identity`, `pre_restore_database`, pre-restore `wal`/`shm`, plus three flat required literal-true properties `no_committed_v6_attestation`, `no_activation_history_record`, and `no_v6_runtime_or_workbench_write`. Those three flat values are copied exactly into the restore receipt's nested `frontier_proof`; there is no nested `frontier_proof` property in the preparation wire shape.

## 5. Top-level schemas

### 5.1 `ActivationGenerationAuthority`

Exact required fields: `schema_version=aar.activation-generation-authority.v1`, `authority_store_id`, `profile_id`, `activation_generation`, `intent_digest`, `profile_digest`, `migration_attestation_digest`, nullable `previous_activation_authority_digest`, and self `authority_digest`.

The strict model validates wire shape/range and self digest. History-tip existence, first/successor selection, monotonicity, CAS, path/filename, chain/fork/current-pointer and terminal-marker equality belong to later operator integration and are not inferred here.

### 5.2 `CutoverPlan`

Exact required fields and domains from `CONTRACTS.md` §3:

- literal schema version;
- epoch/runtime/database identities;
- source `database_file`, `owner`, source registry literal `5`, source schema and canonical-v5 row-set digests;
- `wal`, `shm`, `database_checks`, all-zero `nonterminal_counts`, `snapshot`, `candidate`, migration SQL and activation-intent digests;
- inert `final_profile_output`, profile ID, proposed positive activation generation, nullable prior authority digest;
- `authority_store_id` equal to `owner.authority_store_id`;
- sorted unique `allowed_recovery_actions`, length `1..4`, values from `abort|apply|reconcile|status`;
- `created_at_unix_ms < expires_at_unix_ms`, both `0..9223372036854775807`;
- self `plan_digest`.

The model does not inspect source DB/sidecars or enforce external intent equality.

### 5.3 `OperatorPreparedMarker`

Exact required root fields: schema version, `kind=cutover|restore`, `epoch`, `operator_identity_digest`, `prepared_at_unix_ms`, nullable `cutover`, nullable `restore`, self `prepared_marker_digest`.

- `kind=cutover`: non-null cutover, null restore, root epoch equals `cutover.plan.cutover_epoch`, root operator digest is independent current apply owner, and `plan.created_at <= prepared_at < plan.expires_at`.
- `kind=restore`: null cutover and non-null restore.

The marker embeds no receipt or terminal digest of itself.

### 5.4 `CutoverReceipt`

Implement every required field/domain in `CONTRACTS.md` §4, including all nullable post-commit observations and self `receipt_digest`.

Exhaustive outcome matrix:

- `committed`: `database_commit_state=committed`; non-null migration attestation, activation authority, post-DB digest, checks, generated profile digest and DB-commit time; ordered timestamps.
- `aborted_before_db_commit`: `not_committed`; every post-commit nullable field null; no committed time.
- `recovery_required`: nullable facts remain only when positively observed; it must not claim a successful fully committed state by contradiction.

All outcomes retain exact candidate/intent/snapshot/prepared bindings. Receipt contains `prepared_marker_digest` but no terminal-marker field/digest.

### 5.5 `RestoreReceipt`

Implement every required field/domain in `CONTRACTS.md` §4, including required original cutover terminal digest, restore prepared digest, literal `activation_authority_disposition=unchanged`, exact nested frontier proof and self receipt digest.

- `restored_pre_frontier`: replacement committed; non-null restored digest/checks/replacement time; before/after activation-authority digests equal; all frontier bits true; sidecar disposition from the frozen enum; ordered timestamps.
- `recovery_required`: records uncertainty and cannot claim a successful replacement or authority rollback.

Receipt contains no new terminal marker.

### 5.6 `OperatorTerminalMarker`

Exact required fields: schema version, terminal `kind`, epoch, receipt schema version, complete strict cutover-or-restore receipt, publication time, self `marker_digest`.

Enforce exhaustive mapping:

- `cutover_committed ↔ committed`;
- `cutover_aborted ↔ aborted_before_db_commit`;
- `cutover_recovery_required ↔ recovery_required` cutover receipt;
- `restore_committed ↔ restored_pre_frontier`;
- `restore_recovery_required ↔ recovery_required` restore receipt.

Root epoch equals nested epoch; receipt version and runtime type agree; publication is not earlier than nested completion. Marker contains exact already-digested receipt; nested receipt contains no marker field.

## 6. Digest and issue/build rules

For each of the six top-level documents with self digest, an `issue`/builder may canonicalize only explicitly canonical collections before computing the digest. Compute the digest over the complete validated JSON document with only that root document's own digest omitted; nested documents retain their already-computed digests. Direct model/JSON validation rejects wrong root digest and noncanonical supplied collection order.

Construction order is acyclic:

`plan → prepared marker → transaction/readback facts → cutover|restore receipt → terminal marker`.

Independent tests must recompute each root digest without calling the production digest helper. Reissued nested bytes must change each containing outer digest. Swapped receipt/marker kind, changed plan/epoch, stale inner digest, receipt containing terminal fields, or outer marker tamper must reject.

A module-local registry may contain exactly these six top-level schema-version keys. It is not the canonical cross-module registry owned by A1c.

## 7. RED and focused minimum-sufficient tests

### Assertion-level RED

Before source creation, add only an `importlib.util.find_spec("aar.provider_ready_operator_models")` assertion and run the focused file. It must fail at the assertion with `module is missing`, not during collection/import.

### Behavioral RED

After an importable strict-model skeleton exists, prove at least one wrong outcome/nullability or digest mutation is accepted/misclassified before completing validators. Capture that focused failure; do not use import/collection failure as behavioral RED.

### Focused T0/T1 model tests

**Targeted-negative oracle rule:** every non-digest negative involving a self-digested document MUST use the public issue/builder path to construct the inconsistent candidate or independently recompute that document's otherwise-correct root digest after the targeted mutation, reissuing any changed nested document and every containing outer document as needed. The observed failure must name or otherwise prove the intended union/outcome/epoch/nullability/time/terminal-mapping invariant. A retained stale root or nested digest is valid evidence only in the dedicated digest-tamper tests and MUST NOT satisfy a non-digest negative.

At minimum cover:

1. exact required fields, unknown-field rejection, strict scalar/no-bool coercion and recursive `additionalProperties=false`/required/no-default schema assertions;
2. direct lexical/bound tests for UnixMs, OperatorPath/NUL, package/source IDs, collection min/max/duplicate/order and all-zero nonterminal counts; after local `$defs/$ref` resolution, directly assert `CutoverPlan.model_json_schema()` emits `allowed_recovery_actions.minItems=1`, `maxItems=4`, and the exact item enum `abort|apply|reconcile|status`; generic five-item rejection, error typing, duplicate rejection or order rejection is not a substitute for these emitted-schema assertions;
3. every SidecarObservation state/nullability combination;
4. candidate-binding equivalence to A1a without changing A1a bytes;
5. six independent self-digest oracles, wrong-root rejection and nested digest propagation;
6. activation-authority null/non-null prior digest is wire-valid while external CAS/chain claims remain absent;
7. plan owner/store equality, time order, recovery-action canonicalization and zero-nonterminal rules;
8. cutover/restore prepared exclusive union, epoch/time/binding equality and snapshot/sidecar checks;
9. complete cutover receipt three-outcome nullability/timestamp matrix;
10. complete restore receipt two-outcome/frontier/authority/timestamp matrix;
11. all five terminal kind↔receipt-version↔runtime-type↔outcome mappings; swap/tamper/epoch/time failures;
12. direct introspection proving no terminal field/digest exists in either receipt and no receipt exists in prepared marker;
13. inert import/source boundary: no runtime/provider/MCP/operator/SQLite/filesystem/subprocess/socket/network/env/clock calls or imports;
14. module-local registry exact six keys.

Run only:

```bash
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen pytest -q -p no:cacheprovider tests/test_provider_ready_operator_models.py
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen ruff check --no-cache src/aar/provider_ready_operator_models.py tests/test_provider_ready_operator_models.py
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen ruff check --no-cache --select W291,W293 src/aar/provider_ready_operator_models.py tests/test_provider_ready_operator_models.py
```

No full suite, T2, real SQLite, filesystem mutation, signals/crash process, service control or live call is warranted for this pure-model slice.

## 8. Out of scope / forbidden

- `src/aar/provider_ready_models.py` and its tests;
- activation readback, planner readback, backend availability, workbench grant set and issued grants (A1b-op2);
- provider alias, evaluator classification and paired admission (A1b-eval);
- canonical registry, duplicate-key byte loader, generators, checked-in schemas/fixtures and package assets (A1c/A2);
- operator CLI, migration/bootstrap/snapshot/restore/history/current/lock/reconcile implementation (B lanes);
- runtime composition, grants, planner, admission, facade/MCP/provider/transport (C–E);
- any credential, install, provider request, T4/T5, push, tag or release.

## 9. Stop/reopen conditions

Return `BLOCKED` without widening scope if:

- any required wire property/nullability or restore-preparation frontier layout is ambiguous in the frozen SDD;
- exact strict behavior requires changing A1a, predecessor schemas/canonicalization, shared registry/generator/package files, or more than the two owned paths;
- a requested validator would execute I/O or claim external CAS/history/DB truth;
- focused evidence exposes a distinct T2+ behavior that cannot be represented by pure models.

## 10. Handoff

After final focused GREEN and both Ruff gates PASS:

1. inspect status and diff;
2. stage exactly the two owned paths;
3. local commit only if the dispatch explicitly authorizes it;
4. report starting parent, RED evidence, exact model inventory, focused results, file digests, commit SHA and final status;
5. make no claim beyond A1b-op1 strict-model source readiness.
