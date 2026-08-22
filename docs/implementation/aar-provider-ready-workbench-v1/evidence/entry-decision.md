# AR-PRW Entry Decision

**Decision epoch:** 2026-08-22 (CK implementation authorization)

**Accepted source:** `adaptive-agent-runtime 0.5.0a0` / `bd30df40a9f3e77bcf2d244dbf4fd9bba0148ba1`

**Source-baseline gate:** `SATISFIED_BY_FROZEN_EXACT_BASE_EVIDENCE`

**Writer-dispatch gate:** `A1B_OP2_SATISFIED_A1B_EVAL_PENDING`

## Decision

`IMPLEMENTATION-PLAN.md §1` requires baseline tests and source-delta review to be **recorded** before runtime changes. That condition is already met for the accepted source identity:

- frozen `BASELINE.md` binds the exact accepted commit and records `743 passed, 5 skipped, 1 warning` with zero provider-backed contender calls;
- the reviewed/frozen SDD and structural receipt bind the accepted source identity and requirements/acceptance inventory;
- the active implementation branch was created from that exact commit;
- at the original entry decision, the only delta was copied frozen SDD and PMO control documentation; subsequently reviewed A1a and A1b-op1 product commits are recorded separately and do not retroactively change that baseline fact;
- `evidence/entry-custody.json` records the source/worktree/spec identities and preservation boundary.

A fresh full-suite rerun is therefore supplemental evidence, not an unbounded prerequisite for the first contract-model slice. This interpretation follows the plan's recorded-evidence wording and the minimum-sufficient-test cadence; it does not weaken any touched-boundary or final-candidate gate.

## Supplemental run treatment

- DrvFS attempt `proc_e2e1114e3cda` is `ABORTED_ENVIRONMENT_DIAGNOSTIC`, neither PASS nor a complete pytest FAIL verdict.
- Linux-native exact-source run `proc_d05b65b81812` terminalized PASS: `743 passed, 5 skipped, 1 warning`; Ruff reported `All checks passed!`; the public-safe digest-bound receipt is `evidence/baseline-linux-native-summary.json`.
- This Linux-native PASS does not close the observed DrvFS launcher/supervisor T3/ENV-5 risk.

## Remaining dispatch gates

The first Luna/max writer may start only after:

1. the Generation-10 deterministic S0 successor at semantic tree `sha256:cf6874ee870a1e2bcca3fb83b785213ea8b2f0205e4a54afaf9161b609925ab1` retains its independent `PASS / BATCH_COMPLETE` with `findings=[]`; Generation-9 through Generation-5 are historical blocked generations, and every earlier generation remains non-authorizing;
2. the Generation-10 A1a rev2 packet at SHA-256 `6d2326a68cdbaf7bad0326cd5d28207ffefa9a7f3ceb855b6ecf3d85912798ba` has received independent `PASS / BATCH_COMPLETE`, collection-schema discriminator PASS, collision PASS and `findings=[]` against that exact S0 successor; rev1 at `sha256:92f8f64e538fd8669d28c5ade27649fb61eb8e94f510193b69b149bbc940a6b7` is historical `BLOCKED / BATCH_COMPLETE`, and Generation-9/8/7 packet PASSes remain non-transferable;
3. the exact task-start commit and two-path allowlist are frozen in the dispatch;
4. no shared contract/generator or incompatible writer collides with A1a;
5. Luna/max receives the packet through Baton with explicit RED → GREEN → focused verification → scoped handoff requirements.
Luna A0 seam mapping and the exact-base source baseline are complete. For the current op2 successor, status remains `generation_13_op2_review_pending` and op2 product paths remain absent.

## Current lane-specific successor gate

The original Generation-10/A1a entry gate completed and authorized the focused-green A1a and A1b-op1 commits already recorded in the control plane. It does not authorize A1b-op2 against authority later shown incomplete.

A1b-op2 rev1 `sha256:d5e9a2d729ba628f07bc4679c28c67f0bcad6c8b1784d4739585e9bb0d3216d9` is historical `BLOCKED / BATCH_COMPLETE`. Generation-11 and op2 rev2 now require fresh fixed-byte S0 and packet `PASS / BATCH_COMPLETE` before any op2 writer. Until then, A1a/op1 remain focused-green, op2 product paths remain absent, and the writer gate is HOLD.

Generation-11/op2 rev2 is also historical `BLOCKED / BATCH_COMPLETE`. Generation-12/op2 rev3 is the current fixed-byte successor; it requires fresh dual PASS before exact candidate commit or writer dispatch.

Generation-12 S0 passed, but op2 rev3 remained historical `BLOCKED / BATCH_COMPLETE` by one migration-required underconstraint. Generation-13/op2 rev4 is now the current fixed-byte successor and still requires fresh dual PASS.

Generation-13 S0 and op2 rev4 now have independent terminal `PASS / BATCH_COMPLETE`, `findings=[]` in `deleg_e07daeda`; exact reviewed candidate commit is `e5f0691a88d27fcb635f663cdf6765dc6cc65f97`. Writer dispatch remains gated only by a terminal evidence/task-start commit and repeated two-path collision readback.

A1b-op2 subsequently reached focused-green at `d9e0705fc80d46adf48ae1b9112ce1f05a11e089`, with 22 focused tests and both Ruff gates PASS after central oracle/witness corrections. The next writer gate is A1b-eval packet review; op2 closes no acceptance row by itself.
