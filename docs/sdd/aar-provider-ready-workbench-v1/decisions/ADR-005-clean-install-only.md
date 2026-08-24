# ADR-005 — Clean-install-only provider-ready release, rev2

- **Status:** accepted scope amendment for the superseding `0.6.0a0` specification candidate
- **Revision:** rev2, deterministic clean-install projection and evidence contract
- **Scope:** `docs/sdd/aar-provider-ready-workbench-v1/` only
- **Does not modify:** product source, tests, package assets, predecessor SDD, migration-v6.sql, historical ADR/review records, or BASELINE

## Context

The predecessor provider-ready design treated an existing runtime as a transition authority. The owner selected a narrower release: a fresh target is enough, and an old root can be handled outside product authority when removal is intended. The transition state machine therefore is not needed for the requested outcome.

The existing 14 strict schemas and 64 fixtures, registry-v6 DDL, activation/grant contracts, D1/D2 caller-work/planner ownership, method-scoped admission, asset parity, and evaluation evidence remain required frozen authorities. They are not regenerated. New clean-install integration fixtures and the planned receipt schema are additive future assets.

## Decision

`0.6.0a0` is **CLEAN-INSTALL-ONLY**.

1. The exact sole public mutation is `aar-admin runtime install --runtime-home <absolute-runtime-home> --intent <exact-intent> --candidate-receipt <absolute-json> --wheel <absolute-wheel>`.
2. The target is absolute, NUL-free, safe, and absent in its final component. Existing target forms fail with `FRESH_INSTALL_TARGET_EXISTS` before mutation.
3. Status and activation verification are read-only. No aar-admin cutover command exists. The product does not preserve v0.5 in place, does not adopt an old root, does not downgrade, and does not mutate old-root authority.
4. The installer retains no-follow parent/ancestor handles, creates one exclusive mode-0700 sibling by dirfd, and performs all staging, verification, cleanup, and publication relative/no-follow.
5. The installer uses one exact clean v6 projection: legal `authority-<64hex>` and `empty-v5-<64hex>` tokens, one preparation object without staging paths, positive verified backup, exact attestation payload digest, and integer ordered timestamps.
   The install invocation epoch is exactly `install-` plus 64 lowercase hexadecimal characters, issued once after preflight and projected unchanged to the legacy `cutover_epoch`; the supplied intent must be generation 1 with null predecessor.
6. Prepublication verifies candidate receipt/wheel/assets, factory declarations, route/grant policy, v6, profile, and generation-1 history/current without creating executable capability rows, WorkbenchGrantSet, session grants, or runtime health.
7. Linux `renameat2(..., RENAME_NOREPLACE)` is the sole publication point. EEXIST is refusal; target inode equals staged inode on success; parent fd is fsynced; parent/ancestor replacement is `FRESH_INSTALL_PARENT_REPLACED` and never success. Native Windows fails closed.
8. After publication, supervisor startup allocates/fences normal runtime_generation=1, instantiates exact factories, projects capabilities, persists WorkbenchGrantSet, issues no session grant unless requested under policy, and publishes Ready after readback. Startup never initializes, migrates, repairs, adopts, or rewrites install state.
   C2 is owned by a standalone activation coordinator and exact-generation filesystem store; admin/operator remain thin, and only the minimum optional supervisor/ReferenceHost/MCP injection seam is added. Migration and registry ownership do not move.
9. If an old root exists and removal is intended, an inactive verified external archive/readback is required. On a genuinely fresh host, a proven no-old-root observation is the alternative. A nonexistent archive is not mandatory.

## Necessity record

- **Outcome:** an absent target becomes one complete usable verified v6 runtime or remains absent.
- **Invariant:** no partial, substituted, adopted, inferred, or prematurely Ready target becomes authority.
- **Decision:** `DIRECT/PLATFORM_PRIMITIVE`.
- **Rejected proxy:** an in-place transition state machine, durable recovery ledger, or second broker/workbench.
- **Retained mechanism:** one invocation stage, exact receipt/wheel, complete verification, fsync, and no-replace publication.

## Consequences

The authority surface is smaller and retry behavior is deterministic. Operators own any external archive/removal of an old root. Stale staging may require manual cleanup but is inert and never adopted. The exact product receipt loader and Linux fd-relative primitive remain implementation-only seams. The specification itself does not authorize product edits or live actions.

## Acceptance implication

The machine matrix is generated as **214 atomic rows** with **T0=22, T1=91, T2=73, T3=15, T4=13, T5=0**; 201 T0–T3 rows are release gates. A-TEST-001 is non-counted metadata. The prior aggregate count is not carried forward.
