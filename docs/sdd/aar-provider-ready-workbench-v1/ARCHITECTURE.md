# Architecture

## 1. Design rule

AR-PRW composes existing authorities. It does not hide missing authorities behind configuration booleans.

The minimum preserved invariant is:

> A workbench mutation or physical provider call is admitted only when the current runtime generation, registry-v6 attestation, activation-profile digest, principal/session grant, method adapter, route binding, attempt/ticket fence, and cumulative budget all authorize the same operation.

## 2. Existing mechanisms retained

| Mechanism | Disposition | Successor use |
|---|---|---|
| Durable supervisor and generation-unique private attachment | KEEP | Single runtime owner and restart boundary. |
| Registry v6 workbench/caller-work tables | KEEP | Sole durable workbench lifecycle authority. |
| AR-MB route catalog/binding/receipt/usage journal | KEEP | Route policy, drift rejection, provider usage evidence. |
| Five caller-work commands | KEEP | Claim, send-start, certain no-send cancellation, candidate commit, reconciliation. |
| MCP v8 38-tool surface | KEEP | No schema or order change in this release. |
| Synchronous `RlmWorkbenchPlanner` seam | RESTRICT | Deterministic tests and explicitly native service-managed profiles only. |
| Deprecated MCP Sampling route | REMOVE from new journey | Compatibility only; never qualification authority. |
| Static fake model broker | REFERENCE ONLY | Contract/unit evidence; cannot satisfy product admission. |

## 3. Authorities

| Actor | Owns | Does not own |
|---|---|---|
| Operator / host configuration owner | Cutover epoch, activation profile selection, grant policy, exact package/profile approval | Provider outcomes, AAR operation state, benchmark scores |
| `aar-admin` | Verification and execution of one explicit operator command under exclusive runtime-home custody | Ongoing supervision, credentials, silent upgrades |
| AAR supervisor | Runtime generation, operation/attempt/ticket/fence state, workspace/checkpoint/result finalization | Provider credentials, provider truth, final user delivery |
| Host caller driver | Physical request execution, provider credential lookup, driver-native lookup/cancel observations | AAR terminal state, hidden replay, grant expansion |
| Provider/gateway | Its reported effective route, response identity, and usage | AAR admission or evaluator scoring |
| Evaluator | Fixture custody, clock, acceptance gates, claim matrix | Contender internals or missing telemetry inference |

Any unavailable owner produces a contained state, never an inferred authorization.

## 4. Activation intent and final profile

The operator authors a credential-free canonical activation **intent** before cutover. It binds:

- exact package version, source commit, wheel digest, contract manifest and skill digests;
- runtime-home and database identity and required schema version 6;
- programmable backend and security profile;
- root-planner mode;
- one manifest per native or caller method adapter, each bound to a package-owned immutable factory ID/digest;
- route-catalog digest and allowlisted profile IDs;
- principal/session policy and typed budget/grant ceilings;
- recovery-compatibility inputs;
- cutover authority store ID and activation generation.

The intent MUST NOT contain a token, API key, cookie, provider credential, command substitution, arbitrary executable path supplied by a job, or arbitrary endpoint supplied by a job.

The final host profile is generated, not hand-edited: it wraps the exact validated intent plus the committed migration-attestation digest, then receives a distinct final profile digest. The legacy v6 attestation field named `profile_digest` equals the pre-cutover `intent_digest`; activation/grants bind the post-cutover final `profile_digest`. This acyclic construction is normative in `CONTRACTS.md §0–1`.

The final profile is not self-authorizing. It becomes active only after:

1. schema v6 and the bound migration attestation are read back;
2. intent digest, legacy attestation field, final-profile digest and attestation digest all recompute;
3. every package/contract/skill/candidate digest matches installed bytes;
4. every declared adapter factory loads and reports the exact manifest;
5. grants and route catalog validate;
6. runtime generation is freshly allocated and owns the database;
7. a canonical activation readback is emitted.

Steps 1–5 plus a complete immutable activation-history/terminal-marker scan and exact derived-current-pointer equality form a side-effect-free read-only preflight under the shared runtime-home lock **only when an activation-profile path is explicitly supplied** and before `ReferenceHost` construction or `start_runtime()`. A supplied-but-absent, malformed, stale, or binding-invalid profile fails this activated-start preflight: it allocates no runtime generation, opens no listener, writes no discovery/Ready record, configures no grant issuer, and creates no operation. When no activation-profile path is supplied, the host instead follows the preserved compatibility-start path: it may construct/start exactly as v0.5, publishes only truthful frozen-v8 unconfigured/reference capability rows, never configures a mutation-grant issuer, never migrates, and cannot become `active`. Only a successful activated-start preflight may proceed to host construction, fresh runtime-generation allocation and final history/current identity recheck before Ready publication.

## 5. Capability truth

Capability rows are projections of verified runtime objects, not profile input echoed back. Their exact method set, row shape and capability evidence-tier names remain the frozen v8 schema and six-entry broker catalog.

For each method:

```text
configured =
  profile active
  AND factory ID resolves in the package-owned immutable registry
  AND factory implementation digest matches installed bytes
  AND adapter factory instantiated
  AND manifest digest matches
  AND adapter generation is derived from the current activation/runtime generation
  AND method contract digest matches
```

`backend_kind` is `native`, `caller_driver`, `reference`, or `unconfigured`. A caller driver may be configured while no caller is currently polling; therefore capability output means *an exact adapter contract is installed*, not *a live external process is guaranteed*. Liveness is established only by qualification/operation evidence.

The frozen v8 projection distinguishes configuration from admission usability. Exact allowed native/caller factories project `configured=true`, `reference_only=false`, their current adapter identity/generation and the exact frozen capability evidence tier. Because frozen v6 caller-work omits `artifact.put`, v1 activation rejects an `artifact.put` caller driver; artifact persistence is native or reference-only. An exact reference factory projects `configured=true`, `reference_only=true`, null adapter identity/generation and `evidence_tier=unknown`. Missing, stale or factory-unbound rows project unconfigured. A required method is admission-usable only when `configured=true AND reference_only=false`; reference rows therefore remain truthful without authorizing production work.

The v8 workbench capability output remains the client-facing per-method truth. The operator CLI emits a richer activation readback for installation and release evidence. Frozen v7 `aar_capabilities` bytes remain unchanged.

## 6. Root planner closure

### Observed problem

The v0.5 coordinator invokes an injected synchronous planner for initial, correction, recovery, and finalization directives. The default production host injects none. Cell broker calls can suspend through caller-work tickets, but a caller-delegated root planner cannot.

### Required design

Caller-delegated jobs MUST model every root planner call as `model.request` caller work with this complete durable identity distributed across frozen owners:

```text
(operation_id,
 planner_phase = initial|correction|recovery|finalizer,
 planner_step_index,
 route_binding_digest,
 request_digest,
 directive_schema_digest)
```

The strict frozen `logical_owner_json` contains exactly `kind=planner`, `phase`, and `step_index`; `step_index` equals `planner_step_index` above and no `ordinal` wire field is introduced. `operation_id` is owned by the suspension/ticket relational columns. Route, request, and directive-schema identities are owned by the strict canonical request addressed by `request_digest`.

Flow:

1. coordinator builds the bounded planner request and expected `RlmDirective` response contract;
2. it persists one caller-work suspension/ticket before releasing the attempt;
3. operation projects `waiting_external`; no attempt lease remains active merely to wait;
4. caller claims and marks send-start before the physical request;
5. caller commits an observation or reconciles an unknown outcome;
6. the frozen-v6 cell-free planner CAS uses the unique waiting-external operation event to select the suspended predecessor attempt, verifies its released lease and settled ticket, then advances a pending outbox to `prepared` with one exact successor attempt/fence/generation;
7. if that prepared successor dies, a new claimed attempt may perform the exact generation-advancing `prepared→prepared` takeover only after the old lease and dispatch authority are both dead; a live owner blocks takeover;
8. the current successor rechecks route, usage, request, logical-owner, directive schema, budget, control/cancel/deadline and fence, selects exactly one valid-directive/correction/certain-failure/cancel/deadline outcome, then atomically commits `prepared→consumed` and that outcome's durable event/projection in one registry transaction; an outcome-unknown send remains unconsumed and reconcile-only;
9. a real cell authority is created only in that atomic directive transaction when a real cell is selected; planner flow never reads/writes cell-bound attempt-authority or rebind-transfer rows.

A service-managed profile uses the same strict mode-tagged activation `planner` object and selects its unique native `model.request` manifest/factory from the package-owned immutable method registry. The sole normalization maps job `caller_delegated→caller_delegated_ticketed` and `service_managed→service_managed`; no alias exists. It uses the same directive, route/receipt journal, independent lease keeper, cancellation/reconciliation and capability contracts; there is no second constructor namespace or arbitrary import seam.

## 7. Method-scoped admission

A host need not pretend all six methods exist for a job that cannot invoke them.

The required set is deterministically derived from the frozen job contract and effective grant capabilities. `CONTRACTS.md §8` is normative; in summary:

- caller-delegated root planning requires a ticketed `model.request` caller driver and `start_only=true`;
- service-managed root planning requires a separately activated profile whose same immutable-registry `model.request` manifest is native and exactly projected in planner readback; it cannot borrow caller-driver readiness;
- artifact and subagent budgets/features imply their matching methods;
- `evidence.query`, `effect.propose`, and any otherwise optional model method become required when granted;
- zero budgets and absent grants never imply authority.

Admission first resolves the frozen sorted unique `grant_ids` array into one coherent current issuer set, rejects mixed identity/generation/digest/ceiling records and duplicate capability records, derives the effective capability union, then evaluates normalized mode/budget/feature requirements. The internal semantic class is **authority denied**, but every frozen-v8 MCP failure serializes it as existing wire code `GRANT_DENIED`; `AUTHORITY_DENIED` is never a v8 wire literal. Only after authority succeeds does a same-generation instantiated-adapter health loss yield `CAPABILITY_UNAVAILABLE`; factory/profile/digest drift retires the generation and revokes grants, so it is the same authority-denied/`GRANT_DENIED` outcome. An optional method that is not configured is omitted from planner/facade capabilities.

This release does not alter the frozen v8 input schema. A future explicit job-method allowlist may justify a v9 successor, but it is not required for the first product journey.

## 8. Grant publication

Default/unactivated hosts remain read-only/unconfigured and publish no workbench mutation grant.

After activation, the host may publish bounded grants for:

- `rlm.workbench.execute`;
- `rlm.workbench.read`;
- the five caller-work commands;
- only the adapter methods and budget maxima permitted by profile policy.

`aar.workbench-grant-set.v1` is the server-side policy authority. Its self digest binds exact principal policy, exact-request-session binding, capability set, runtime and activation generations, profile/history-tip/capability/route digests, budget ceilings and TTL. Every minted grant additionally creates a server-side issued record bound to one exact principal/session and those same values. The client context is only compared against issuer state and never creates authority.

The supervisor configures the issuer only after factories/capabilities and history/current equality validate, then publishes Ready/readback with the same `grant_set_digest` as the final startup step. Every mutation looks up `grant_id` and compares the full server record before operation creation. On shutdown/deactivation Ready is removed before issuer revocation. Grants expire or become stale on TTL/deadline/runtime-generation retirement; profile/factory/route/history change always advances runtime generation. Profile activation alone performs no inference and creates no operation.

## 9. Provider route and evidence

The first qualified profile binds:

```text
provider_driver = openai-codex
provider        = openai-codex
model           = gpt-5.6-luna
reasoning       = max
fallback        = none
cache           = disabled (unless the protocol freezes another scope)
```

Reuse `ModelRouteBinding`, `ModelRouteReceipt`, `ModelUsageRecord`, caller ticket identity, physical attempt identity, request digest, candidate/command receipt digests, and model execution journal. Do not create a parallel token ledger or a third runtime observation-tier enum.

The evaluator-owned strict `aar.evaluation-evidence-classification.v1` is the only classification owner. It independently projects provider/model/reasoning/fallback/cache tiers (`attested | observed | requested_only | contradicted`), derives `aar_live_qualified | prime_live_qualified | insufficient | contradicted`, and separately projects usage tier plus attempt visibility from exact frozen/runtime/native receipt inputs. Capability rows remain configuration metadata with the frozen values `unknown | caller_observed | host_receipt_bound | provider_attested`; they may cause a downgrade/rejection but never upgrade any evaluator component. Prime may retain requested-only reasoning with null effective effort while other components are observed. Unknown retry, fallback, physical request, or token values remain null/unknown rather than zero.

## 10. Compatibility

- Exact v7 capability bytes remain frozen.
- Exact v8 38-tool names/order/input/output schemas remain frozen.
- Existing v1 RLM, workspace, assets, public caller-delegated product, and no-code public plugin behavior remain unchanged.
- Without an activation profile, v0.6 behaves like the truthful unconfigured v0.5 host.
- A v5 database is never auto-migrated at ordinary server start.
- A v6 database/profile from a newer release is rejected by older code unless exact backward compatibility is proven; no silent downgrade.

## 11. Complexity budget

Allowed additions:

1. one `aar-admin` console entry point;
2. fourteen canonical operator/profile/evaluation document schemas, including strict operator prepared/terminal markers, paired-evaluation admission, append-only activation-generation authority, server-side workbench grant-set, and evaluator evidence-classification records;
3. one activation-profile loader/factory composition path;
4. caller-delegated root-planner ticket integration;
5. method-scoped admission and grant projection;
6. focused tests, package assets, skill/profile updates, and qualification adapter updates.

Disallowed without an SDD amendment:

- new daemon;
- new durable database;
- new provider credential store;
- new provider SDK embedded in AAR core;
- new MCP inference back-channel;
- new MCP tool or mutation of frozen v7/v8 bytes;
- generic plugin ecosystem or remote arbitrary code loading.
