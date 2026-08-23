# AR-PRW Implementation Tasks

Status vocabulary: `planned`, `blocked`, `doing`, `source_ready`, `focused_green`, `integrated`, `verified`, `not_authorized`.

| Task | Lane | Status | Dependencies | Owned acceptance rows | Deliverable / gate |
|---|---|---|---|---|---|
| P0 | PMO custody | integrated | — | A-CUST-001 | Exact v0.5.0a0 worktree plus imported, digest-bound SDD; canonical dirty tree preserved. |
| P1 | PMO entry baseline | verified | P0 | — | Exact-base full pytest `743 passed, 5 skipped, 1 warning`; Ruff PASS; DrvFS diagnostic remains separate T3/ENV-5 risk. |
| A0 | Lane A read-only scout | integrated | P0 | A-ACT-001, A-COMP-001, A-SEC-001 | Exact authority graph, writable paths, RED tests, generator/package joins. PMO source-check complete; no writes. |
| S0 | SDD successor for activation/provider-ready authority domains | verified | P1, A0 | contract prerequisites; no implementation row closure | Generation-13 S0 independently PASS/BATCH_COMPLETE with findings=[]; exact candidate commit `e5f0691a88d27fcb635f663cdf6765dc6cc65f97`. |
| A1a | Activation/adapter strict models | focused_green | S0 | prerequisite slice; no row closure alone | Commit `3ca321fd0df5a20a969965a0c1705fdd02dc83b6`; 15 focused tests PASS and both Ruff gates PASS after PMO repaired strict-tuple false-positive tests. Exactly two owned files; no registry/generator/package edits. |
| A1b | Operator/readback/evaluation contract models | focused_green | A1a | contract prerequisites for A-MIG-008/A-MIG-009/A-MIG-010 and later B/C/E/G rows | Op1/op2/eval focused-green; eval product `d406f4d0c6c5997e72beb7c9733a7955367ce836`, 50 focused tests and both Ruff gates PASS. No row closure or launcher/attempt authority. |
| A1c | Contract registry/generators/fixtures | doing | A1b | A-ACT-001, A-SEC-001 | A1c-R focused-green. A1c-GF rev3 packet/spec Terra/high PASS/BATCH_COMPLETE with findings=[] at packet commit `ea6254f11abb2c108f5c4e8233dc991484998d7c`; writer gate pending. No T0 row closure yet. |
| A2 | Compatibility/package projections | blocked | A1c | A-COMP-001, A-NEC-001, A-REL-001 | Frozen v7/v8 byte parity, no-profile v0.5 compatibility, supplied-invalid profile pre-host refusal and generator→reviewed→bundled/package join; contract freeze review required. |
| B1 | Read-only operator boundary | blocked | A2 | A-MIG-001, A-OPS-001 | `aar-admin` plan is canonical-stdout-only and plan/status/verify remain zero-write/zero-checkpoint; no service control or hidden mutation. |
| B2 | Cutover/snapshot/attestation/generation CAS | blocked | B1 | A-MIG-002..A-MIG-005, A-MIG-008, A-MIG-010, A-ACT-005 | Stable lock, canonical empty-v5 bootstrap that stops before cutover, separately materialized ordinary plan authority, SQLite backup API after exclusive ownership, acyclic strict prepared/receipt/terminal markers, atomic v6 DDL+attestation, append-only per-profile history CAS plus derived current and exact rerun/conflict. |
| B3 | Reconcile/restore frontier | blocked | B2 | A-MIG-006, A-MIG-007, A-MIG-009, A-MIG-010, A-ACT-005 | Explicit exact-input cutover/restore reconcile across snapshot/prepared/DB/profile/immutable-history/derived-current/terminal boundaries, no implicit apply/abort/blind replacement/down migration, and one exact original-cutover-terminal→restore-committed closure for pre-frontier restore. |
| C1 | Activation composition | blocked | A2, B2 | A-ACT-002..A-ACT-005 | Exact profile/intent/attestation/unbranched-history/derived-current/marker/factory verification; caller/native model.request planner factory projection in readback; frozen reference truth and fail-closed Ready publication. |
| C2 | Grants/status/no-inference | blocked | C1 | A-AUTH-001, A-AUTH-002, A-AUTH-003 | Strict server-side grant-set plus complete sorted-unique grant_ids resolver, exact coherent-set/principal/session/profile/history/capability/route/budget/TTL binding, issuer ordering/revocation, authority-before-availability precedence, exact readback and zero provider sends. |
| D1 | Durable planner phases | blocked | A2 | A-PLAN-001..A-PLAN-003 | Null-cell root-planner ticket/suspension for every `{kind, phase, step_index}` owner; unique predecessor event; cell-free v6 pending prepare, dead-owner prepared takeover and atomic consumed+directive commit; zero cell-bound authority/rebind access; wait releases attempt ownership and remains accepted. |
| D2 | Settlement/recovery/send lineage | blocked | D1 | A-PLAN-004..A-PLAN-006, A-ROUTE-004 | Exhaustive valid-directive/correction/certain-failure/cancel/deadline consume projections, outcome-unknown no-consume/reconcile-only, authoritative receipt gate, null-preserving usage, recovery no-replay and send-start lineage. |
| E1 | Method-scoped admission | blocked | C2, D2 | A-ADM-001..A-ADM-005 | Mandatory release-gate native/caller model.request × normalized job/profile mode × budget × resolved coherent grant set × factory cross-product and constructible GRANT_DENIED-vs-CAPABILITY_UNAVAILABLE classes. |
| F1 | Installed product assets | blocked | B3, C2, D2, E1 | A-COMP-002, A-SEC-001 | Script, bundled profile/skill/contracts/fixtures and default fail-closed installed host. |
| V1 | Immutable acceptance candidate | blocked | F1 | A-TEST-001 plus all mandatory T0-T3 rows | One source/wheel/profile identity, preserved-v5 and empty-runtime-v5-bootstrap journeys, full gates once, independent current-byte review. |
| G1 | Optional live qualification | not_authorized | separate CK authority | T4 rows only | No provider request in this activation; T5 paired execution has no row or product authority in v1. |

## Baton worker packet contract

Every writer packet must name:

1. exact base commit/tree and active worktree;
2. one coherent task and acceptance-row allowlist;
3. exclusive writable paths plus forbidden paths;
4. RED discriminator and expected failure reason;
5. focused GREEN/lint/generator commands;
6. generated/provenance bytes invalidated by the change;
7. required diff, status, test output, and local commit handoff;
8. explicit no-live/no-secret/no-push boundary.
9. exact `in_scope` behavior and paths;
10. explicit `out_of_scope` behavior, paths, new surfaces and infrastructure;
11. minimum-sufficient test tiers selected because they can falsify the changed claim;
12. concrete reopen/escalation trigger for adding broader scope or a higher test tier.

Only one writer may own shared registries, generators, schemas, fixtures, package assets, or PMO ledgers at a time. PMO—not a worker—merges separate worktrees, runs shared acceptance, and changes row status.
