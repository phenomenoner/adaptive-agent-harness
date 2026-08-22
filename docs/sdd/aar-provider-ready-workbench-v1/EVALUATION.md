# Luna/max Qualification and AAR-vs-Prime Evaluation Admission

## 1. Boundary

This document plans evaluation. It authorizes no provider call, Prime run, AAR workbench run, rerun, or benchmark expansion.

The required treatment is:

```text
provider = openai-codex
model = gpt-5.6-luna
reasoning_effort = max
fallback = none
```

AAR and Prime remain unmodified contenders. Evaluator adapters may freeze public launch envelopes, clocks, fixtures, outputs, and native evidence; they may not patch contenders or infer missing internal telemetry.

## 2. Causal treatments

### AAR arm

The complete treatment is:

1. exact activated AAR candidate;
2. caller-delegated root-planner and cell ticket lifecycle;
3. host caller driver executing exact ticketed requests;
4. operation-scoped IPython workbench;
5. AAR reconciliation/finalization/materialization boundary.

The dispatcher is not the contender. A valid AAR attempt proves that AAR created and owned every planner/broker ticket that caused the physical request.

### Prime arm

Stock Prime Agent Minion with:

- `--provider hermes-codex`;
- `--model gpt-5.6-luna`;
- `--thinking max`;
- frozen tool/session/context policy;
- no source patch, preload hook, private instrumentation, or hidden resumed state.

Launch flags prove requested configuration only. Effective-route evidence is promoted only by stock/native observations that actually exist.

### Prime provider-alias rule

`hermes-codex` is a launcher alias, not automatically an evidence-grade synonym for `openai-codex`. The owning schema, read-only builder/validator, and evidence location are normative in `CONTRACTS.md §10`. Before paired admission, the strict `aar.provider-alias-attestation.v1` MUST bind:

```text
launcher_alias = hermes-codex
canonical_provider_identity = openai-codex
wire_api = openai-codex-responses
prime_executable/config/model-registry digests
credential resolver identity/digest (never credential bytes)
model = gpt-5.6-luna
requested_reasoning = max
alias_digest
```

The digest domain follows `CONTRACTS.md §0`. Without this document, Prime's provider identity is `requested_only`. With it, provider identity may be normalized for requested-treatment comparison, but effective model/effort and usage retain only the strongest stock/native evidence Prime actually emits. A launch flag or alias attestation alone never becomes provider-signed effective-route proof.

## 3. Evidence tiers

### Route components

Provider, model, reasoning, fallback and cache are classified independently as `attested`, `observed`, `requested_only`, or `contradicted` under `CONTRACTS.md §11`. `requested_only` always keeps that component's effective field null; one component's stronger evidence never upgrades another. The derived qualification is `aar_live_qualified`, `prime_live_qualified`, `insufficient`, or `contradicted`.

This admits an honest asymmetric Prime record: native output may observe effective canonical provider/model/fallback/cache while effective reasoning remains null and launch-bound requested reasoning remains `requested_only`. It does not collapse that mixed state into either a fully observed route or a wholly requested-only route.

### Usage

| Tier | Meaning |
|---|---|
| `request_receipt` | Complete provider-reported request rows tied to physical attempts and receipts. |
| `session_aggregate` | Native aggregate without complete request lineage. |
| `partial_events` | Some usage exists but scope/coverage is incomplete. |
| `unavailable` | No defensible provider usage evidence. |

### Attempt visibility

`complete`, `aggregate_only`, or `unknown`. Unknown retry/fallback/physical request/token counts remain null, never zero.

The evaluator MUST materialize exactly one strict `aar.evaluation-evidence-classification.v1` record per qualification arm/attempt and may bind both `classification_digest` values into one later `aar.paired-evaluation-admission.v1` planning document. `CONTRACTS.md §11` is the exhaustive projection algorithm and §12 owns only the immutable paired-plan binding; it owns no physical send or replay authority. The five route-component tiers, derived route qualification, usage tier and attempt visibility have no other owner. Frozen runtime capability `evidence_tier` is retained only as a downgrade consistency input and never upgrades the evaluator record.

## 4. AAR qualification request/receipt

AAR qualification MUST bind:

- run/operation/ticket/physical-attempt IDs;
- planner phase/`step_index` or cell broker owner;
- request and response digests;
- requested and effective provider/model/effort;
- route profile/catalog/policy digests;
- fallback chain and cache scope;
- provider response ID/model when available;
- provider-reported input/output/total and nullable reasoning/cache usage;
- retry ordinal and whether output contributed to final artifact;
- send-start, terminal receipt, reconciliation, and commit timestamps;
- adapter ID/generation, frozen capability evidence metadata, and evaluator `classification_digest`.

Existing `ModelRouteBinding`, `ModelRouteReceipt`, `ModelUsageRecord`, caller-work ticket/claim/physical-attempt IDs, command/candidate receipts, and model journal remain the source evidence. The strict evaluator classification owns only the deterministic tier projection and source-digest list. The benchmark adapter projects one join; neither adapter nor capability metadata creates stronger provider authority.

| Qualification field | Owning evidence | Requirement |
|---|---|---|
| run/case/arm/attempt | frozen evaluation manifest | non-null and unique within protocol |
| operation/planner owner/suspension | workbench job + strict logical owner + suspension row | exact operation, phase, frozen-wire `step_index` (planner ordinal) and revision |
| ticket/claim/physical attempt | caller-work ticket and command receipts | non-null; revisions and adapter generation current |
| request bytes/digest | canonical ticket request | recomputed digest equals durable value |
| requested route/effort/fallback/cache | job `ModelRouteBinding` and catalog/policy digests | exactly `openai-codex` / `gpt-5.6-luna` / `max` / `none` / frozen cache policy |
| effective provider/model/effort | `ModelRouteReceipt` plus accepted provider/gateway observation, projected by classification record | non-null at `attested`; otherwise route tier is downgraded, never inferred |
| response identity/digest | provider response ID/model plus candidate/model receipt | response digest recomputes; nullable provider ID is explicitly marked unavailable |
| send/reconcile/settle/commit times | command receipt and successor settlement | ordered, nonnegative, tied to same ticket/attempt |
| input/output/total usage | `ModelUsageRecord` for every physical attempt | required nonnegative provider-reported integers for AAR qualification |
| reasoning/cache usage | same record | integer when provider reports it; otherwise explicit null with evidence scope |
| retry/contribution | physical-attempt lineage and finalization manifest | every attempt represented; discarded/cancelled spend remains counted |

Usage arithmetic is normative:

1. no absent dimension is converted to zero;
2. each recorded integer is nonnegative and retains provider semantics;
3. per-attempt `total_tokens` must satisfy the equation declared by the qualified adapter/provider contract; an undeclared or failing equation is `USAGE_EVIDENCE_UNAVAILABLE`;
4. aggregate input/output/total values equal the sum of all in-scope physical-attempt rows, including retries and discarded output;
5. if any required AAR attempt row is absent, aggregate token-efficiency claims fail rather than using a partial denominator;
6. nullable reasoning/cache dimensions propagate as unknown for metrics that require them.

For Prime, stock JSON/event usage may satisfy `session_aggregate` or `partial_events` only after scope and arithmetic checks. It is never transformed into request-level visibility without stock request IDs/rows.

## 5. Route qualification

Before any paired attempt:

1. freeze exact AAR wheel/source/profile/route/driver/skill/contract digests;
2. freeze exact Prime executable/config/launch digest;
3. run all T0–T3 no-inference gates;
4. authorize one separately named qualification request per arm;
5. use a trivial, non-scored prompt with tools disabled except what the treatment intrinsically needs;
6. verify requested/effective route and evidence tier;
7. reconcile usage scope and physical-attempt visibility;
8. record any asymmetry in the claim matrix;
9. stop on contradiction, fallback, effort downgrade, missing required AAR receipt, duplicate spend, or unclassifiable timeout.

After both arms are live-qualified, the evaluator may build one unexpired strict `aar.paired-evaluation-admission.v1` that binds those exact classification/alias/profile/candidate bytes, case/fixture/oracle/artifact/tool/protocol/budget/stop-matrix digests, explicit operator planning authority and exact arm order. Missing, expired, changed, or duplicate-run-ID input is rejected while building the document. This document does not allocate an attempt, prove a physical-request count, or authorize T5 spend; qualification evidence and T0–T4 never authorize an implicit run.

Qualification calls never become benchmark attempts and their tokens remain in a separate overhead ledger.

## 6. Negative route probes

AAR qualification must deliberately reject, before benchmark admission:

- provider mismatch;
- model mismatch;
- effort lower than `max` or unavailable effective effort where attestation is required;
- non-empty fallback chain under `none`;
- route/profile/catalog digest mismatch;
- arbitrary endpoint or credential-bearing route input;
- missing request/response/ticket/physical-attempt lineage;
- usage marked provider-reported but absent or arithmetically inconsistent;
- stale adapter generation;
- duplicate idempotency key causing a second physical send;
- crash after send-start without lookup/reconciliation;
- candidate receipt from a stale claimant;
- hidden retry without per-attempt usage rows when token efficiency is claimed.

## 7. Paired claim matrix

Comparable when fixtures and controls match:

- deterministic hidden gates and accepted artifact;
- completion/failure/timeout classification;
- evaluator wall-clock;
- externally observable operator intervention;
- requested route from frozen launch/profile;
- main-agent orchestration calls/turns and AAR transfer overhead.

Comparable only if evidence scope matches:

- effective route/effort;
- provider token totals;
- quality per token;
- retry/fallback/wasted-token metrics.

Not comparable by default:

- hidden Prime physical request count;
- hidden Prime retry/fallback count or tokens;
- provider-signed evidence when neither provider supplies it.

Richer AAR governance evidence is a separate scorecard and adds no artifact-quality points.

## 8. Future paired protocol is not current execution authority

No paired instrumentation block is admitted, launched, scored, retried, or expanded by v1/`0.6.0`. The immutable `aar.paired-evaluation-admission.v1` value is planning evidence only: it may bind exact candidate/profile/alias/classification/case/fixture/oracle/protocol/budget/stop-matrix digests and arm order, but no package component consumes it and it allocates no physical attempt.

A future separately reviewed launcher-authority SDD must define physical attempt allocation, partner-arm ordering, send-start, stop/no-rerun handling, crash/reconcile, materialization reuse, replay fences and incident authority before any paired spend. Contradiction, drift, leakage, timeout/outcome-unknown, receipt/arithmetic failure, shared-infrastructure failure, and evaluator-materialization failure are merely bound future protocol categories here; they do not create current machine actions or acceptance rows. No T5 call, block, winner, rerun, or workload recommendation is authorized in this candidate.

## 9. Admission statuses

| Status | Requirement |
|---|---|
| `not_qualified` | No live route evidence. |
| `qualification_failed` | Contradiction or required evidence gate failed. |
| `requested_treatment_qualified` | Frozen Prime launch plus valid alias attestation makes provider/model/reasoning/fallback/cache requested components valid, but provider/model remain `requested_only` and `route_qualification=insufficient`. This status cannot admit a paired benchmark. |
| `live_qualified` | AAR classification is `aar_live_qualified` with request-level usage; Prime is `prime_live_qualified` because stock/native evidence observes effective canonical provider, Luna, no-fallback and disabled-cache with no contradiction, while max may remain component-wise `requested_only` with `effective_reasoning=null`. |
| `benchmark_planned` | Frozen case/protocol/budget, both arms `live_qualified`, exact precommitted planning inputs, and one valid unexpired `aar.paired-evaluation-admission.v1` planning document with explicit operator planning authority. This status is not T5 launch permission. |

No v1 status authorizes T5 physical execution. `requested_treatment_qualified`, `live_qualified`, and `benchmark_planned` are evidence/planning states only; live paired spend remains `NOT AUTHORIZED` pending a future durable launcher-authority SDD.

No formal winner or workload recommendation is available before scored paired evidence.

## 10. Budget and reruns

- T4 provider calls require explicit authorization separate from implementation.
- Every physical request, including correction, retry, recovery, cancelled/discarded output, and verification, is charged.
- AAR planner calls are primary/coordination/recovery/finalizer by logical owner; no planner spend is hidden as evaluator overhead.
- Evaluator orchestration and route-qualification calls remain separate.
- Unknown monetary cost is null, not estimated silently.
- Paired phase execution/expansion does not exist in v1; a future launcher-authority SDD and separate CK authorization are both required.
