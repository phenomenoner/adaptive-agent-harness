# Luna/max route qualification and evaluation boundary — rev2

## 1. Boundary

This document plans evaluation only. It authorizes no provider call, AAR workbench run, Prime run, rerun, benchmark expansion, or paired physical execution. T4 is separately authorized only after one exact candidate passes the 201 T0–T3 atomic clean-install gates.

Required treatment remains:

```text
provider = openai-codex
model = gpt-5.6-luna
reasoning_effort = max
fallback = none
cache = disabled unless a separately frozen protocol says otherwise
```

AAR and Prime remain separate contenders. Evaluator adapters may freeze launch envelopes, clocks, fixtures, outputs, and native evidence; they cannot patch contenders or infer missing telemetry.

## 2. AAR arm

The AAR arm uses the exact installed candidate that passed the absent-target T3 gate, its profile/history/current, caller-delegated root-planner tickets, host caller driver, operation-scoped workbench, and reconciliation/finalization boundary. Candidate, profile, route, contract, skill, wheel, and asset identities are bound to the exact receipt and installed readback. T4 cannot rebuild or select another candidate.

## 3. Prime arm and alias

Stock Prime uses `--provider hermes-codex`, `--model gpt-5.6-luna`, `--thinking max`, frozen tool/session/context policy, no source patch, no preload hook, and no hidden resumed state. Launch flags prove requested configuration only. The strict `aar.provider-alias-attestation.v1` binds launcher alias, canonical provider identity, wire API, exact executable/config/model-registry/provider-entry/credential-resolver executable digests, model, and requested reasoning. It contains no credential bytes and never attests effective route or usage.

## 4. Component-wise evidence

The sole `aar.evaluation-evidence-classification.v1` owner independently classifies provider, model, reasoning, fallback, and cache as `attested`, `observed`, `requested_only`, or `contradicted`. `requested_only` keeps its effective field null. One component cannot upgrade another and runtime capability metadata cannot upgrade evaluator evidence.

Derived qualification is `aar_live_qualified`, `prime_live_qualified`, `insufficient`, or `contradicted`:

- AAR requires all five components, exact ticket/physical-attempt lineage, and request-level provider usage.
- Prime may qualify with sufficient observed/attested provider/model/fallback/cache evidence and honest reasoning evidence; requested `max` alone does not prove effective reasoning.
- Weak mixes remain insufficient; contradiction remains contradiction.

## 5. Usage and visibility

| Tier | Meaning |
|---|---|
| `request_receipt` | complete provider-reported request rows tied to physical attempts |
| `session_aggregate` | complete session aggregate without request rows |
| `partial_events` | incomplete scope or coverage |
| `unavailable` | no defensible usage evidence |

Attempt visibility is `complete`, `aggregate_only`, or `unknown`. Unknown retries, fallback, physical requests, or token values remain null, never zero. AAR request receipt requires every in-scope attempt, provider-reported integers, exact arithmetic, and complete lineage.

## 6. Qualification receipt and sequence

An authorized qualification receipt binds run/case/arm/attempt, operation/planner owner/suspension, ticket/claim/physical attempt, request, route/catalog/policy, requested/effective route, response, send-start/settle/reconcile/commit times, adapter/runtime generation, usage, retry/contribution lineage, and classification digest.

Before T4: freeze exact wheel/receipt, installed evidence, Prime alias evidence, route and asset digests; confirm T0–T3; authorize one low-cost request per scenario; verify effective route and usage; reconcile no-duplicate-spend lineage; stop on contradiction, fallback, effort downgrade, missing receipt, arithmetic failure, stale generation, or indeterminate timeout. Qualification calls are separate overhead and never paired benchmark attempts.

## 7. Required negative evidence

T4 must reject provider/model/reasoning/fallback/cache drift, route/profile/catalog mismatch, arbitrary endpoints or credentials, missing ticket/attempt/response/usage lineage, estimated or inconsistent usage, stale generation, duplicate idempotency send, ambiguous send without reconciliation, stale receipt claimant, and hidden retry without per-attempt rows.

## 8. Paired planning and statuses

After both arms are separately qualified, an optional unexpired `aar.paired-evaluation-admission.v1` may bind exact candidate/profile/alias/classification/case/fixture/oracle/artifact/tool/protocol/budget/stop-matrix bytes and operator planning authority. It allocates no attempt, calls no provider, and is not consumed by a package component.

No v1 status authorizes T5 physical execution. A future launcher specification must define allocation, order, send-start, stop/no-rerun, crash/reconcile, replay fences, and incident authority before paired spend. This document claims no implementation, installed candidate, provider call, live qualification, paired run, score, winner, or benchmark.
