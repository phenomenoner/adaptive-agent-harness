# AR-PRW Entry Decision

**Decision epoch:** 2026-08-22 (CK implementation authorization)

**Accepted source:** `adaptive-agent-runtime 0.5.0a0` / `bd30df40a9f3e77bcf2d244dbf4fd9bba0148ba1`

**Source-baseline gate:** `SATISFIED_BY_FROZEN_EXACT_BASE_EVIDENCE`

**Writer-dispatch gate:** `HOLD_PENDING_S0_AND_PACKET_REVIEW`

## Decision

`IMPLEMENTATION-PLAN.md §1` requires baseline tests and source-delta review to be **recorded** before runtime changes. That condition is already met for the accepted source identity:

- frozen `BASELINE.md` binds the exact accepted commit and records `743 passed, 5 skipped, 1 warning` with zero provider-backed contender calls;
- the reviewed/frozen SDD and structural receipt bind the accepted source identity and requirements/acceptance inventory;
- the active implementation branch was created from that exact commit;
- the only current delta is the copied frozen SDD and PMO implementation-control documentation; no product source or test file has changed;
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
Luna A0 seam mapping and the exact-base source baseline are complete. Until the remaining conditions hold, status remains `spec_successor_review_pending` and product source remains unchanged.
