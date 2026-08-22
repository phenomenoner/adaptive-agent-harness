# Luna/max Independent Operations and Evaluation Review

- Model: `gpt-5.6-luna`
- Reasoning: `max`
- Mode: bounded read-only review of attached specification bytes
- Verdict: **CHANGES REQUIRED**
- Review scope: `README.md`, `BASELINE.md`, `MIGRATION.md`, `EVALUATION.md`, `ACCEPTANCE.md`, `IMPLEMENTATION-PLAN.md`, requirements, and acceptance matrix
- Files modified by reviewer: none

## BLOCKERS

1. **Packet incompleteness.** Architecture, contracts, lifecycle, WAL, and handoff were outside this bounded packet, so the review could not approve the whole SDD. This is a packet-scope limitation; it correctly requires a final integrated closeout.

2. **Cutover/supervisor race is not closed.** The admin lock is not explicitly shared by supervisor startup, so a supervisor could start after the read-only owner check and before migration commit. **[MIGRATION.md §§3–5; MIG-002; A-MIG-002]**

## MAJOR FINDINGS

1. **WAL/SHM safety is underspecified.** Exact sidecar disposition, fsync/readback, and safe restore are missing. **[MIGRATION.md §§4–7; MIG-003; MIG-006; MIG-007]**

2. **Crash-state and rollback precedence are ambiguous.** A state containing v6 objects without an attestation must remain `RECOVERY_REQUIRED` and must not be restored through the ordinary pre-frontier path. **[MIGRATION.md §§6–7]**

3. **The planner test could pass with a synchronous implementation.** `A-PLAN-001` needs a synchronous-planner canary plus direct durable ticket/settlement/resume inspection. **[PLAN-001; A-PLAN-001]**

4. **Prime route identity is inconsistent.** AAR uses canonical `openai-codex`, while Prime launch uses `hermes-codex`; no alias/equivalence contract is stated. **[EVALUATION.md §§1–2; ROUTE-001]**

5. **`live_qualified` may be awarded with requested-only Prime evidence.** Requested intent and effective-route qualification need distinct status labels. **[EVALUATION.md §§3, 9; LIVE-001]**

6. **AAR receipt acceptance is not field-complete.** Matrix rows do not individually require every route/usage/attempt/timestamp/digest/evidence field and nullable arithmetic rule. **[ROUTE-002; ROUTE-003; ROUTE-004; A-ROUTE-001 through A-ROUTE-003]**

7. **Paired stop rules lack discriminating rows.** Contradiction, drift, timeout, receipt failure, and materialization failure each require a no-rerun/no-expansion row. **[EVALUATION.md §8; A-EVAL-001]**

8. **Method-scoped admission lacks a normative cross-product.** Model, artifact, subagent, evidence, effect, native, and caller-delegated variants need an exact derivation table and tests. **[ADM-001; ADM-002]**

## MINOR FINDINGS

- Fresh-v6 initialization needs an explicitly different receipt class from migration.
- Snapshot evidence should assert size, WAL/SHM state/digests, and parent-directory fsync.
- Frozen v7/v8 compatibility must compare authoritative paths/hashes, not only derived manifests.
- Plan expiry must be checked immediately before every mutation.

## VERIFIED STRENGTHS

- Source custody and non-authorization are clear.
- The design rejects needless new infrastructure.
- Migration ordering is substantially sound.
- Idempotency, forward-only rollback, and recovery-required states are recognized.
- Activation is separated from inference and grant publication.
- T0–T5 claim boundaries are explicit.
- The paired protocol forbids silent reruns and preserves failed attempts.
- Every listed requirement has at least one acceptance row.

## REQUIRED EDITS

1. Complete the integrated packet with WAL/handoff and final cross-review.
2. Define shared cutover/startup fencing.
3. Add WAL/SHM backup/restore state table.
4. Add synchronous-planner negative canary.
5. Define canonical provider versus launcher alias and Prime requested/effective evidence statuses.
6. Expand route/usage receipt assertions.
7. Split paired stop-rule acceptance rows.
8. Add job-to-method derivation table and cross-product tests.
