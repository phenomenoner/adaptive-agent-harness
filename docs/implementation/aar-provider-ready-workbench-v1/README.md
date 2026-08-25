# AR-PRW Implementation Control Plane

**Status:** `a1c_gf_packet_pass_writer_pending`

**Control epoch:** `2026-08-22T22:17:48Z`

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
| Historical reviewed Generation-10 semantic tree | `sha256:cf6874ee870a1e2bcca3fb83b785213ea8b2f0205e4a54afaf9161b609925ab1` |
| Historical blocked Generation-11 semantic tree | `sha256:feaa05ebecbb935163cfce48a606e00feedbf7a77d2c49cb49ddaa27927a362b` |
| Historical S0-PASS/op2-blocked Generation-12 semantic tree | `sha256:090fdfce7aa7d2afd74200c683bd817af8ac525e088e42ea27fc6234b02a3933` |
| Active Generation-13 semantic tree | `sha256:f676ed30a08e3f9cbfe38b091f69c69857fe7da047612a964565a76d0ab702fb` |
| Active Generation-13 complete tree | `sha256:d9373a26bb56e3604b08857eb81b0770bbcfd5f81f8d6174f89e130256330d6d` |
| `requirements.json` file SHA-256 | `50b41b0552807d92eb209aad4bee61688852cd2a93e88b3deef245dd90870290` |
| `acceptance-matrix.json` file SHA-256 | `0bf0d9e7f341282f2db0e22240a43df850e16f302faff97e441a5a01716ecdc1` |
| Generation-13 spec receipt file SHA-256 | `c416093655ad0d2c311f132ea1e0e6408d8d60222b74cf9ea6cd04a2de2d3d8c` |
| A1a rev2 packet SHA-256 | `6d2326a68cdbaf7bad0326cd5d28207ffefa9a7f3ceb855b6ecf3d85912798ba` |
| A1b-op1 packet SHA-256 | `35a962b1388787ee10390872380a64028074ad5b3afec7bb490f725ab062a7d8` |
| A1b-op1 product commit | `d03a10f4ac1cc18b43addb0e62245073f7274e3e` |
| A1b-op2 rev4 packet SHA-256 | `30b0eb7f5fad76fccee96c46ad9ca2f69900c6438fcb6d00ea59d7219fd090ac` |
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
- [x] Reviewer-discovered `SPEC_GAP` is closed by a reviewed deterministic SDD successor.
- [x] First RED-first writer packet has exclusive writable paths, named acceptance rows, and independent PASS.

## Current implementation evidence

- A1a is focused-green at `3ca321fd0df5a20a969965a0c1705fdd02dc83b6`: its exact two files have 15 focused tests PASS and both Ruff gates PASS after PMO corrected a strict-tuple test false positive. No acceptance row closes from this prerequisite alone.
- A1b-op1 is focused-green at `d03a10f4ac1cc18b43addb0e62245073f7274e3e`: its exact two files implement six operator-authority top-level schemas plus ten nested strict records, with 15 focused tests PASS and both Ruff gates PASS. PMO corrected only formatting/typing and one strict-list collection test seam before final green. No operator I/O/runtime behavior or acceptance row closure is claimed; A1b-op2 and A1b-eval remain pending.
- A1b-op2 rev1 review was `BLOCKED / BATCH_COMPLETE`: it exposed the missing readback `unconfigured` backend domain, incorrect reference-row truth, unfrozen issued-grant timestamp fields, overconstrained source-dependent readback nullability and test-oracle false-pass seams. Generation-11 amends only those not-yet-implemented op2 authorities and validator discriminators; A1a/op1 product bytes and evidence remain unchanged. Rev2 independent review is pending.
- Generation-11/op2 rev2 dual review was also `BLOCKED / BATCH_COMPLETE`: it found the configured-reference/current-generation contradiction, unauthorized profile-verified catalog/tool/route erasure, an external manifest-tier equality claim inside a pure row, missing sibling-axis validator markers and incomplete positive-witness families. Generation-12/rev3 minimally repairs those exact findings; dual review pending.
- Generation-12 S0 passed, and op2 rev3 closed every prior finding but remained `BLOCKED / BATCH_COMPLETE` by one migration-required underconstraint: capability digest and configured native/caller rows were not explicitly forbidden. Generation-13/rev4 adds only that null/method restriction plus exact one-axis test/validator probes; review pending.
- Generation-13 S0 and op2 rev4 received independent `PASS / BATCH_COMPLETE`, `findings=[]` in `deleg_e07daeda`; exact reviewed candidate commit is `e5f0691a88d27fcb635f663cdf6765dc6cc65f97`. Writer remains pending terminal evidence commit and post-commit collision readback.
- A1b-op2 is focused-green at product commit `d9e0705fc80d46adf48ae1b9112ce1f05a11e089`: exactly the runtime readback source/test files, 22 focused tests PASS, full Ruff PASS and W291/W293 PASS after PMO repaired digest-oracle and witness-completeness seams. No runtime observer, issuer, I/O, T2+ execution or acceptance-row closure is claimed. A1b-eval remains pending.
- A1b-eval rev1 was `BLOCKED / BATCH_COMPLETE`; rev2 packet `sha256:b29c453f2bbda7cb3f48081cc18c6ea5f10459261dde489a259e5f93603889cb` closed all five findings and received independent `PASS / BATCH_COMPLETE`, `findings=[]` in `deleg_c40c636e`. Exact packet commit is `0b91f748cda1573621166c4020b1027345a30356`; writer task-start/collision gate pending.
- A1b-eval is focused-green at product commit `d406f4d0c6c5997e72beb7c9733a7955367ce836`: exact two-file ownership, 50 focused tests PASS and both Ruff gates PASS after PMO closed composition-revalidation, re-digested-extra and authority-consumer-scan seams. It is pure planning/evidence only and closes no acceptance row alone. A1c is next.
- A1c-R rev1 was `BLOCKED / BATCH_COMPLETE`; rev2 packet `sha256:c0d703b30c04524f9b60521cc5427b4633e9fce40fe4cff4b1f7849f49bfb1af` closed all five findings and received independent `PASS / BATCH_COMPLETE`, `findings=[]` in `deleg_c288c446`. Exact packet commit `84088a36d55ec40588c3cfde75fdc81f5cb09502`; legacy `schema_profile.py` is truthfully excluded as known-incompatible, not represented as PASS.
- A1c-R is focused-green: worker product `51a09742105bbc040bfe9e626aff8e9f86dc7563`, PMO test-oracle follow-up `0048d4110d9a272b67a05ce5b9360a28036a5de2`, 27 focused tests and both Ruff gates PASS. Production registry/loader bytes were unchanged by the follow-up; no generated asset or acceptance-row closure is claimed. A1c-GF is next.
- A1c-GF rev1 and rev2 remain historical `BLOCKED / BATCH_COMPLETE` candidates. Rev3 packet `sha256:ce11b9de0a85938b892becc93e24e1d2a4f4fea935813475ae6756a372292092` plus normative spec raw `sha256:a49428b890f0c8e612fe677b24ca4fd3decc83ad71674a699eddc8f7403605a8` / self `sha256:1b45466190c7b54d493ef3d68857633f5b8f2643cd827a602faa976aafc85379` received Terra/high `PASS / BATCH_COMPLETE`, `findings=[]` in `deleg_462ba005`; packet commit `ea6254f11abb2c108f5c4e8233dc991484998d7c`. Writer task-start/collision gate pending.

## Evidence policy

The normative requirement and acceptance text remains in the frozen SDD. This directory stores execution status and evidence bindings only; it must not fork the contract. Worker self-reports are advisory until PMO reads the bytes, checks scope, and reruns the named discriminator. T4 rows stay `not_authorized` and cannot be inferred from T0-T3 results; T5 execution has no row or product authority in this release.
