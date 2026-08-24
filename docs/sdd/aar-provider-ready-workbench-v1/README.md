# AR-PRW — Provider-Ready RLM Workbench (clean-install rev2)

**Status:** superseding specification candidate; specification-only, not an implementation claim.

**Release mode:** `CLEAN-INSTALL-ONLY`. The `0.6.0a0` claim is limited to constructing one complete runtime below an absent, safe target and publishing it once. The product does not preserve v0.5 in place, does not adopt an old root, does not downgrade, and does not mutate old-root authority.

**Exact sole public mutation:**

```text
aar-admin runtime install --runtime-home <absolute-runtime-home> --intent <exact-intent> --candidate-receipt <absolute-json> --wheel <absolute-wheel>
```

`runtime status` and `activation verify/status` are read-only. No aar-admin cutover command exists in this release. No v5 initializer or transition mutator is reachable from the v0.6 public surface.

## Scope and custody

This rev2 package changes only the 16 paths named by the task. The three pre-existing dirty product paths remain task-start bytes, and the frozen native migration-v6.sql remains an input. This package does not reset, clean, stage, commit, push, install, start a service, call a provider, or mutate a runtime.

The package proves documentation structure and planned acceptance only. It does not prove that the command exists, that a wheel was built, that an install occurred, that startup reached Ready, or that a provider was called.

## Frozen compatibility boundary

The existing 14 strict schemas and 64 fixtures remain exact compatibility/evidence bytes and are not regenerated. The count language is **14 preserved + 1 planned receipt schema**: `aar.install-candidate-receipt.v1` is a new planned strict public schema, not a live fifteenth asset. New clean-install integration fixtures and receipt fixtures are additive future implementation assets.

Unchanged v7/v8, registry-v6 DDL, D1/D2 caller-work ownership, planner, grants, admission, route/usage, asset, and evaluation contracts remain required. The five transition-shaped schemas are inert evidence assets and do not authorize a command or state transition.

## Clean-install outcome

The observable outcome is one complete v6/profile/history/current/package/factory tree or an absent target. One mode-`0700` invocation staging directory (mode-0700) and one Linux `renameat2(..., RENAME_NOREPLACE)` publication are the only added mechanisms. Existing targets return FRESH_INSTALL_TARGET_EXISTS. Native Windows fails closed with `FRESH_INSTALL_PUBLICATION_UNSUPPORTED` in v0.6.

The target algorithm is frozen before database creation:

1. Require an absolute path with no NUL; require the final component to be absent.
2. Require every existing ancestor, including the parent, to be a UTF-8-encodable non-symlink directory. Resolve the existing parent strictly.
3. Set `canonical_target = resolved_parent / original_final_component`. Do not Unicode-normalize, case-fold, or reinterpret the final component.
4. Encode canonical JSON as UTF-8 with POSIX `/` separators. Compute exactly:

   ```json
   {"database":"<canonical_target>/reference.sqlite3","runtime_home":"<canonical_target>"}
   ```

   as `runtime_home_digest`. The issuer, installer, staging verifier, and startup verifier use this same object and byte algorithm.
5. Compute `database_identity` as `db-` plus the lowercase hexadecimal part of `canonical_sha256({"schema_version":"aar.clean-install-database-identity.v1","runtime_home_digest":...,"database_name":"reference.sqlite3"})`.

Raw/unresolved path text, inode/dev identity, and staging paths are never semantic identity. Inode/dev are temporal fencing facts only.

Clean-install accepts only an intent with `activation_generation=1` and `previous_activation_authority_digest=null`. After descriptor/target preflight, the installer freezes one `install-<64 lowercase hex>` invocation epoch; a new invocation never reuses that token or adopts its stale stage.

## Temporal safety and generation order

The installer retains no-follow directory handles for the resolved parent and required ancestor identity chain, recording fstat dev/ino/mode/owner. It creates staging by retained-parent dirfd-relative exclusive creation with mode `0700`, retains the staging fd/dev/ino, performs all work relative and no-follow, and refuses cleanup on identity drift. It never recursively deletes a path after a string comparison.

Before and after publication it verifies the parent/ancestor path identity chain. Linux `renameat2(parent_fd, stage_name, parent_fd, target_name, RENAME_NOREPLACE)` is the linearization point. `EEXIST` is refusal; the target inode must equal the staged inode; the parent fd is fsynced. Parent/ancestor replacement returns `FRESH_INSTALL_PARENT_REPLACED` with contained/manual evidence, never success. Same-principal kernel/handle attacks beyond these deterministic substitution tests are outside the `trusted_local` claim.

Before publication, C1 verifies the candidate receipt, wheel bytes, fixed assets, factory declarations, route policy, grant policy, v6, profile, and generation-1 history/current. It does **not** build executable capability rows, `WorkbenchGrantSet`, session grants, or runtime health because `runtime_generation` does not yet exist. After publication, C2 transitions to `starting`, allocates/fences the normal runtime generation, and invokes one standalone activation coordinator. Its exact-generation store publishes and reads back the grant set before Ready; session grants are current-generation memory-only.

The implementation boundary is standalone-first: focused install models, `aar.runtime.installer`, `aar.runtime.provider_ready_activation`, and focused tests own new behavior. `aar-admin` and `runtime.operator` remain thin adapters; supervisor/ReferenceHost/MCP receive only the minimum explicit C2 injection. Existing migration and registry ownership do not move.

## Candidate evidence

The exact receipt and wheel are explicit command inputs. Both are absolute existing regular non-symlink files, opened once with no-follow descriptors and retained fstat identity. The exact wheel bytes are hashed and inspected as ZIP bytes; duplicate, traversal, backslash, absolute, symlink, or noncanonical member names are rejected. Startup revalidates the copied credential-free receipt and installed distribution member digests; it does not recompute the overall wheel digest from installed files. T3 separately proves installed distribution provenance from the exact wheel.

## Planned acceptance and status

`verification/acceptance-matrix.json` contains **214 counted atomic rows**: **T0=22, T1=91, T2=73, T3=15, T4=13, T5=0**. The mandatory no-live release set is **201 T0–T3 atomic rows**. `A-TEST-001` is a non-counted exact-wheel summary in metadata, not an acceptance cell. Counts are generated from the JSON and bound by the final receipt.

The current status remains `spec_planned`. `implementation_verified`, `live_qualified`, and `benchmark_planned` are separate later claims. No implementation, installation, Ready, provider qualification, release, or benchmark is claimed here.
