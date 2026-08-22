# AR-PRW Implementation Control Plane

**Status:** `a1a_focused_green_a1b_packet_pending`

**Control epoch:** `2026-08-22T19:17:55Z`

**Accepted implementation base:** `adaptive-agent-runtime 0.5.0a0` / `bd30df40a9f3e77bcf2d244dbf4fd9bba0148ba1`

**Implementation branch:** `codex/aar-provider-ready-workbench-v1-impl`

**Active SDD successor:** [`../../sdd/aar-provider-ready-workbench-v1/README.md`](../../sdd/aar-provider-ready-workbench-v1/README.md)


## Authority and claim boundary

CK authorized local planning and implementation on 2026-08-22 and required all product source/test implementation to be delegated to Luna at `max`. The historical imported SDD remains preserved by digest; PMO may amend only the active successor specification/control bytes until their current-byte review gate closes.

Authorized now:

- PMO planning, task decomposition, worker dispatch, local integration, local commits, T0-T3 verification, and evidence maintenance;
- product source/test changes only through bounded Luna/max worker packets;
- deterministic local/no-network test and package work needed by mandatory T0-T3 rows.

Conditionally authorized only after **all non-live implementation phases and exact-candidate gates** converge:

- install the completed successor candidate;
- PMO-led Luna/max installed-candidate verification;
- final documentation update;
- prepare the exact push/PR/tag/release evidence and GO/HOLD packet after every mandatory non-live release gate is green; external publication still requires a separate CK decision.

Not authorized before that all-phase terminal gate:

- intermediate-phase installation, push, tag, package publication, or release;
- T4 provider inference, T5 paired evaluation, production/runtime cutover, secret access, or other external effects without separate explicit authority;
- edits to CK's dirty canonical worktree;
- a second workbench, broker service, provider client, credential store, daemon, durable database, or MCP inference back-channel.

`spec_successor_pending` may advance to `implementation_in_progress` only after the reviewer-discovered contract gaps have a reviewed, deterministic SDD successor and the corrected first writer packet passes independent review. Planning artifacts do not promote implementation or verification status.

## Custody and frozen identities

| Item | Identity |
|---|---|
| Accepted source commit | `bd30df40a9f3e77bcf2d244dbf4fd9bba0148ba1` |
| Historical imported SDD complete tree | `sha256:bb0287e1718033c773e763096b202591307458e1524ab8de9ea8d5a29039afee` |
| Active Generation-10 semantic tree | `sha256:cf6874ee870a1e2bcca3fb83b785213ea8b2f0205e4a54afaf9161b609925ab1` |
| Active Generation-10 complete tree | `sha256:fb4dbd69802191f411c6df9281b57e8ca45085012179ff56c403ca940dfbef3a` |
| `requirements.json` file SHA-256 | `50b41b0552807d92eb209aad4bee61688852cd2a93e88b3deef245dd90870290` |
| `acceptance-matrix.json` file SHA-256 | `0bf0d9e7f341282f2db0e22240a43df850e16f302faff97e441a5a01716ecdc1` |
| Generation-10 spec receipt file SHA-256 | `bd63c52ce6eb88bc6ceac7a5adb1d80f546bfbe2cd7eae08728b39e5ca4154f2` |
| A1a rev2 packet SHA-256 | `6d2326a68cdbaf7bad0326cd5d28207ffefa9a7f3ceb855b6ecf3d85912798ba` |
| Acceptance inventory | 45 rows: 39 mandatory T0-T3 release gates, 6 non-release T4 rows, and zero T5 rows |

The dirty canonical worktree remains preserved on `codex/public-plugin-marketplace`; the active implementation worktree is the dedicated `aar-provider-ready-workbench-v1` checkout on `codex/aar-provider-ready-workbench-v1-impl`. Local filesystem paths are intentionally omitted from this public control document.

## Delivery DAG

```text
P0 custody + frozen SDD import
  -> P1 exact-base baseline and source-delta gate
  -> A contracts/generators freeze
       -> B operator cutover -----------+
       -> D durable root planner -------+-> E method-scoped admission
              B -> C activation/grants -+              |
                                                       v
                                                F installed assets
                                                       |
                                                       v
                                           V immutable T0-T3 batch

G optional T4 live qualification = NOT AUTHORIZED; T5 execution absent
```

B and D may later run in separate worktrees only after A freezes and PMO publishes non-overlapping path ownership. Shared registries, schemas, generators, and generated assets remain single-owner.

## Roles

- **PMO (Hermes/Lady H):** scope and dependency control, Baton gate, worker briefs, diff review, integration, evidence invalidation, acceptance ledger, independent review, and final claim.
- **Luna/max workers:** only the assigned product source/tests/generated outputs, with explicit writable/forbidden paths and focused commands.
- **Independent reviewer:** current-byte read-only review after a named freeze; never substitutes for executed acceptance evidence.

## Entry gate

- [x] Exact accepted commit exists locally.
- [x] Clean active worktree created outside cleanroom.
- [x] Dirty canonical branch left in place without reset, clean, rebase, or overwrite.
- [x] Reviewed SDD copied and structurally revalidated; frozen digest/counts match.
- [x] Exact-base pytest terminal: `743 passed, 5 skipped, 1 warning`.
- [x] Exact-base Ruff terminal: `All checks passed!`.
- [x] Luna/max Lane A seam map source-checked by PMO.
- [ ] Reviewer-discovered `SPEC_GAP` is closed by a reviewed deterministic SDD successor.
- [ ] First RED-first writer packet has exclusive writable paths, named acceptance rows, and independent PASS.

## Evidence policy

The normative requirement and acceptance text remains in the frozen SDD. This directory stores execution status and evidence bindings only; it must not fork the contract. Worker self-reports are advisory until PMO reads the bytes, checks scope, and reruns the named discriminator. T4 rows stay `not_authorized` and cannot be inferred from T0-T3 results; T5 execution has no row or product authority in this release.
