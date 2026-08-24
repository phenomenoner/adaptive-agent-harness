# Architecture — clean-install provider-ready rev2

## 1. Outcome and necessity

The observable outcome is: given a credential-free intent, one validated candidate receipt/wheel, and an absent safe target, produce one complete usable registry-v6/profile/history/current/package/factory tree, or leave the target absent.

The minimum invariant is no partial, substituted, adopted, inferred, unverified, or prematurely Ready target. The design decision is **`DIRECT/PLATFORM_PRIMITIVE`**: direct staged construction supplies the fresh runtime, and Linux `renameat2` with `RENAME_NOREPLACE` supplies the one required publication CAS. No second workbench, broker, provider client, credential store, daemon, database, or recovery ledger is needed.

The five transition-shaped schemas remain inert compatibility/evidence assets. Their existence does not create public transition authority.

## 2. Owners

| Owner | Owns | Explicitly does not own |
|---|---|---|
| Operator | exact intent, exact candidate receipt/wheel inputs, optional external archive | provider result, runtime grants, product mutation of an old root |
| Clean installer | target preflight, retained handles, staging, v1-v6 preparation, profile and generation-1 install evidence, final verification, one publication attempt | runtime generation, session grants, provider work, old-root mutation |
| Staged SQLite/registry | v1-v6 schema, attestation, immutable install evidence, retained D1/D2 owner tables | credentials, external wheel proof, runtime health |
| Standalone activation coordinator | one post-generation factory/capability injection, immutable generation grant-set store, memory-only session grants | schema migration, registry ownership, listener/transport ownership |
| Supervisor | startup readback, narrow coordinator invocation, Ready after coordinator readback | schema/profile/install initialization, migration, repair, adoption |
| Caller driver/provider | physical request and provider observations outside this package | AAR authority, grant expansion, replay |
| Evaluator | route, usage, visibility classification and inert paired planning | launch authority, missing telemetry, capability upgrades |

## 3. Exact target identity

The issuer, installer, staging verifier, and startup verifier all use this algorithm, without aliases:

- Linux/WSL v0.6 path support requires an absolute NUL-free target, absent final component, existing UTF-8-encodable non-symlink directory ancestors, and strict resolution of the existing parent.
- `canonical_target` is the resolved parent joined with the original final component. There is no Unicode normalization or case folding.
- Canonical JSON is UTF-8 and uses POSIX `/` separators.
- `runtime_home_digest = canonical_sha256({"database": str(canonical_target / "reference.sqlite3"), "runtime_home": str(canonical_target)})`.
- `database_identity = "db-" + canonical_sha256({"schema_version":"aar.clean-install-database-identity.v1","runtime_home_digest":runtime_home_digest,"database_name":"reference.sqlite3"})` with the `sha256:` prefix removed.

Path text that has not gone through this algorithm, inode/dev values, and transient staging names cannot enter a semantic digest. Native Windows has no v0.6 constructible publication path and returns `FRESH_INSTALL_PUBLICATION_UNSUPPORTED` before publication.

## 4. Temporal ownership

The installer opens and retains no-follow handles for the trusted resolved parent and the ancestor identity chain. It records dev/ino/mode/owner using fstat. Staging is created by an exclusive parent-dirfd-relative operation with mode `0700`; its fd and dev/ino are retained.

Every staging operation, verification, cleanup, and publication is relative to retained handles and no-follow. Cleanup first compares retained identity with current fstat identity; drift refuses deletion and leaves inert evidence. A path string is never sufficient authority for recursive deletion. Before and after publish, the complete target-to-parent/ancestor identity chain is checked.

SQLite's existing pathname APIs are reused only through the installer-owned Linux `SQLiteStageAdapter`. It first proves `/proc/self/fd/<stage_fd>` names the retained stage dev/ino, passes fixed database/backup children below that fd root, and fstats plus no-follow-enumerates database/backup/WAL/SHM state before and after each existing helper. Missing `/proc` support or any identity/entry drift fails closed before the next mutation. This adapter is not a new registry or migration owner.

Publication is exactly:

```text
renameat2(parent_fd, stage_name, parent_fd, target_name, RENAME_NOREPLACE)
```

`EEXIST` is `FRESH_INSTALL_TARGET_RACE`/target refusal. Successful readback must show the target inode equals the retained staged inode, followed by parent-fd fsync. Parent or ancestor replacement is `FRESH_INSTALL_PARENT_REPLACED`; the invocation is contained/manual and never reports success. The matrix uses deterministic barriers and substitution, not sleeps. Attacks by a same-principal malicious kernel or handle outside these deterministic tests are outside `trusted_local`.

## 5. Construction order

1. C1 parses strict intent and exact receipt/wheel inputs; verifies initial activation generation 1/null predecessor, identity, route policy, grant policy, factory declarations, package assets, and frozen v6/profile/activation-generation-1 history/current prerequisites without runtime-generation writes.
2. Create one retained-handle-owned staging directory.
3. Construct canonical empty v1-v5 using the existing `OperationRegistry` transaction.
4. Verify the positive-size empty-v5 backup, then execute unchanged v6 DDL plus attestation in one SQLite transaction.
5. Reopen staged v6 read-only and verify all objects, attestation, integrity/FK, empty domain, clean projection, and digests.
6. Build final profile and generation-1 immutable history/current. Copy the exact validated receipt to `authority/install-candidate-receipt.json`.
7. Verify the complete staged tree and fsync files/directories.
8. Publish once with `renameat2`.
9. Only after publication does C2 allocate/fence the normal runtime generation and invoke the standalone activation coordinator. Its generation store publishes and reads back `authority/runtime-generations/<runtime_generation:020d>/workbench-grant-set.json` before Ready.

Prepublication does not fabricate capability rows, `WorkbenchGrantSet`, session grants, or runtime health. A session grant is issued only when requested under server policy after Ready barriers.

## 6. Capability, grants, planner, and admission

Executable native/caller capability rows require exact package-owned factory/member/implementation digests and the current runtime generation. Reference rows remain truthful with `configured=true`, `reference_only=true`, null adapter identity/generation, and `evidence_tier=unknown`; they are not admission usable. Unconfigured rows are false and unusable.

The standalone activation coordinator creates a server-owned `WorkbenchGrantSet` only after normal generation allocation and factory projection. `ProviderReadyActivationStore` owns exact-generation atomic publication/idempotent readback/conflict behavior; session grants remain memory-only and restart-invalidated. Sorted unique `grant_ids` are resolved against one coherent current set. Missing, revoked, expired, mixed, duplicate, wrong-session, budget, generation, profile, route, or digest authority is `GRANT_DENIED`; only a valid current same-generation health loss is `CAPABILITY_UNAVAILABLE`.

## 7. Standalone module boundary

New behavior is split by responsibility: strict receipt/install models, `aar.runtime.installer` for preflight/staging/publication, and `aar.runtime.provider_ready_activation` for post-generation capability/grant ownership. `aar-admin` is a thin parser/dispatcher; `runtime.operator` remains read-only status/verification. Supervisor, `ReferenceHost`, and MCP receive one optional explicit activation injection required by C2. Migration and registry ownership remain where they are; no second daemon, database, transport, broker, or system seam is introduced.

Caller-delegated planning remains durable `model.request` work with `{kind, phase, step_index}`, released waiting leases, send-start before physical dispatch, reconciliation for uncertainty, cell-free successor CAS, and no access to cell-bound authority/rebind tables. D1/D2 semantics, method-scoped admission, route/usage classification, and no-blind-replay rules remain unchanged.

## 7. Compatibility and non-claims

The existing 14 strict schemas and 64 fixtures remain exact frozen bytes. New clean-install integration fixtures and the planned receipt schema are additive future assets. Frozen v7/v8, migration-v6.sql, D1/D2 owner bytes, and historical ADR/review files are not rewritten.

This is a specification only. It does not claim product implementation, a built wheel, an installed target, a supervisor process, Ready, provider visibility, live qualification, or T5 execution.
