# Luna/max Follow-up Blocker Closure Review 1

- Model: `gpt-5.6-luna`
- Reasoning: `max`
- Verdict: **BLOCKERS REMAIN**
- Scope: current revised specification plus both first-round review reports

## Blocking findings

1. `profile_digest` and `migration_attestation_digest` still formed an unsatisfiable cross-document hash cycle: the final profile included the attestation digest while the attestation was said to include the same final profile digest.
2. `aar.provider-alias-attestation.v1` lacked an owning generated schema/builder/validator and evidence location, and the spec did not say whether requested-only Prime evidence could satisfy `live_qualified` or admit a benchmark.
3. Traceability for package-owned adapter factories was wrong: `ACT-004` pointed at activation-status row `A-ACT-004` rather than the capability projection row.

## Non-blocking notes

- Fresh-v6 and restore receipts needed exact field contracts.
- Budget ceiling units/arithmetic and activation-generation versus runtime-generation needed explicit relationships.
- Final freeze still required a validator receipt and handoff status update.

## Verified closures from the first round

The reviewer confirmed closure of the v6 planner field map, sidecar-state prohibition, cutover locking/CAS/takeover, WAL/SHM backup and restore handling, grant issuer ordering and revocation, package-owned factories, native/caller admission separation, synchronous-planner canary, route/usage lineage and nullable arithmetic, and split stop/no-rerun acceptance rows.

## Remediation applied after this review

- Added acyclic `activation intent -> v6 attestation -> generated final profile` digest construction. The legacy v6 attestation field `profile_digest` now explicitly equals `intent_digest`; final-profile digest is distinct.
- Added strict alias-attestation schema ownership, read-only builder/validator behavior, evidence location, and `requested_treatment_qualified` versus `live_qualified`/`benchmark_ready` rules.
- Relinked `ACT-004` to `A-ACT-003`; `A-ACT-004` now owns readback under `ACT-002`.
- Added strict fresh-v6 and restore receipts, typed budget units/arithmetic, and generation relationships.

This report records reviewer evidence; the remediation claims still require the next independent follow-up review.