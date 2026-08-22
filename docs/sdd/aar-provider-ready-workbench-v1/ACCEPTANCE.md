# Acceptance and Claim Gates

## 1. Evidence tiers

| Tier | Purpose | Provider inference | Required for release claim |
|---|---|---:|---:|
| T0 | Contract generation, schema fixtures, static caller/sink inventory, package/profile byte parity | No | Yes |
| T1 | Cutover/activation unit and fault-injection evidence | No | Yes |
| T2 | Runtime workbench/caller lifecycle integration on exact source bytes | No | Yes |
| T3 | Exact-wheel, preserved-v5 and empty-runtime-v5-bootstrap host qualification | No | Yes |
| T4 | Separately authorized exact Luna/max route and receipt qualification | Yes | No; required for `live_qualified` |
| T5 | Paired AAR-vs-Prime instrumentation/benchmark | Yes | No; downstream evaluation only |

`implementation_verified` requires every T0–T3 release row. T4 and T5 never repair a failed T0–T3 row.

## 2. Claim rules

- Source tests do not establish installed-wheel behavior.
- Standalone MCP discovery does not establish live-host pickup.
- Capability rows do not establish driver liveness or provider treatment.
- Launch flags do not establish effective route.
- A passing artifact does not repair unknown outcome, unauthorized replay, or telemetry fabrication.
- Historical v0.5 test/release evidence remains historical and is not rewritten as v0.6 evidence.
- Every rerun has a new attempt ID and reason; no latest-success overwrites prior failure.

## 3. Required T0 evidence

1. Generated strict schemas and canonical fixtures for all fourteen normative documents: activation intent, final profile, method adapter, activation-generation authority, cutover plan, operator prepared marker, cutover receipt, restore receipt, operator terminal marker, activation readback, workbench grant set, provider-alias attestation, evaluation evidence classification, and paired-evaluation admission.
2. Cross-validator agreement, explicit self-digest domains/equality relations, acyclic `plan→prepared→receipt→terminal` construction, inner/outer marker swap rejection, and digest stability.
3. Exact v7 and v8 MCP assets unchanged byte-for-byte.
4. Static inventory of migration callers, supervisor construction, grant publication, planner calls, provider send sinks, candidate commit/reconcile sinks, and profile/package copies.
5. Credential canaries and secret scan of profile/receipt/log artifacts.
6. Requirement-to-test traceability with zero unowned mandatory rows.

## 4. Required T1 evidence

- read-only plan accepts no output path, emits exactly one canonical JSON document on stdout, produces no filesystem/DB/service mutation, executes no WAL checkpoint, creates no temp/snapshot/authority artifact, and leaves SQLite/filesystem write canaries at zero;
- active/replaced owner, nonterminal state, corrupt DB, WAL drift, stale plan, and intent/final-profile/candidate drift reject;
- shared-supervisor/exclusive-operator lock exclusion, startup rejection, exact-input cutover reconcile and pre-DB `abort` contracts, replacement-owner classification, and lock-loss boundaries; runtime initialization owns no separate epoch/reconcile namespace;
- SQLite backup API after exclusive ownership with read-only-plan DB/WAL/SHM hashes, backup result, fsync, integrity/FK, snapshot-before-prepared adoption/conflict, strict restore preparation/reconcile/replacement, and sidecar-disposition evidence;
- exact rerun is idempotent; changed bytes conflict;
- rollback/restore is denied beyond the frontier;
- activation profile unknown/secret/shell/arbitrary-endpoint fields reject;
- adapter-factory/manifest and grant/route/recovery binding mismatches reject;
- absent/uninitialized runtime initialization runs only the existing canonical-empty-v5 transaction and then stops at `migration_required`; caught pre-commit faults roll back and caught post-commit faults leave canonical empty v5. Abrupt process loss with any WAL/SHM residue fails `UNINITIALIZED_RUNTIME_RESIDUE` with no checkpoint/delete/adoption/retry. A separate operator `cutover plan` plus `cutover apply` later emits the ordinary truthful snapshot/row-set-bound cutover receipt; non-empty v5 is refused by initialize.

## 5. Required T2 evidence

- caller-delegated root planner creates one durable ticket and releases attempt ownership while waiting; a fail-if-called synchronous planner canary remains at zero calls;
- initial/correction/recovery/finalizer identities and suspension revisions are distinct, deterministic, mapped through exact waiting-external operation events, and recovered by fenced prepared takeover plus atomic consume of exactly one valid-directive/correction/certain-failure/cancel/deadline projection on exact v6 rows; outcome unknown remains unconsumed/reconcile-only;
- claim/send-start/cancel/commit/reconcile ordering is enforced;
- crash after send-start is not replayed;
- stale claimant/adapter/attempt/runtime/profile writers are fenced;
- mandatory release-gate method-scoped admission rows pass separately activated caller/native planner-profile × artifact budget × subagent budget × optional grant × exact model.request/method-factory cross-products, including model-only partial profiles;
- feature-implied but ungranted fails `GRANT_DENIED`; current-grant same-generation instantiated-adapter health loss fails `CAPABILITY_UNAVAILABLE`; factory/profile/digest drift retires/revokes and fails `GRANT_DENIED`; optional unavailable invocation fails certain before send;
- grants are absent before activation; afterward the frozen sorted-unique `grant_ids` array resolves every server-side issued record, rejects mixed sets/identities/ceilings and duplicate capabilities, derives one effective capability union, and binds principal/session, budgets/TTL/deadline, runtime/activation/profile/history-tip/capability/route digests; changed client context never self-authorizes;
- planner directive and final output validate before mutation/success;
- cumulative usage includes correction/recovery/retry/discarded spend;
- restart, deadline, cancellation, and finalization races preserve one terminal authority;
- v1/public/workspace/assets compatibility remains green.

## 6. Required T3 evidence

Two fresh, disposable roots are required:

1. **Preserved-v5 upgrade root:** exact supported v5 fixture/database plus WAL/SHM variants → plan/apply/restart/readback/workbench no-inference scenario.
2. **Empty-runtime bootstrap root:** exact wheel/profile install → absent/uninitialized DB → existing Registry canonical empty v5 → ordinary plan/apply v6 cutover/activation/restart/readback/workbench no-inference scenario.

For each:

- installed `direct_url`/wheel/source/profile/skill/contract digests match;
- durable supervisor identity and schema/profile readback match;
- current host/session discovers exact 38 tools and calls workbench capabilities natively;
- compatibility smoke and focused workbench journey pass;
- stop/restart/reconcile leaves no orphan worker/socket/claim;
- run from built wheel, not source import leakage.

Supported Python/platform matrix follows release policy; platform-gated skips remain explicit.

## 7. T4 qualification

T4 uses exactly one authorized request per required negative/positive scenario and is never folded into T0–T3. Minimum positive route is `openai-codex / gpt-5.6-luna / max`, fallback none. Minimum negatives cover route/effort/fallback/profile/usage/duplicate-spend/reconcile drift.

AAR route qualification requires every field join and nullable/arithmetic rule in `EVALUATION.md §4` at request-receipt scope plus one valid `aar.evaluation-evidence-classification.v1` projection bound by its digest. Prime has separate alias/effective-route and usage-scope rows: `hermes-codex` becomes comparable requested provider identity only through the `CONTRACTS.md §10` schema/builder/validator and digest-bound alias attestation, while stock events independently classify provider/model/reasoning/fallback/cache and usage. Prime may be live-qualified with observed provider/model/fallback/cache plus requested-only reasoning whose effective value stays null; weaker mixes remain insufficient. Alias plus launch flags alone yields `requested_treatment_qualified`, not `live_qualified`. No cross-arm artifact, wall-clock, token-efficiency, winner, or workload comparison is authorized by T4 qualification.

## 8. No T5 execution authority

V1/`0.6.0` defines no T5 attempt, launch path, paired block, machine stop action, rerun, score, comparison, winner, or expansion acceptance. One valid unexpired strict `aar.paired-evaluation-admission.v1` planning document may bind exact candidate/profile/alias/classification/case/fixture/oracle/artifact/tool/protocol/budget/stop-matrix bytes, arm order, and explicit planning authority, but it contains no attempt allocation and no package component consumes it. `A-EVAL-007` is the sole machine row and proves this no-launch boundary. Any future paired execution requires a separately reviewed launcher-authority SDD and new acceptance authority.

## 9. Machine-readable matrix

`verification/acceptance-matrix.json` is the row authority for planned acceptance. It records required evidence class, whether live inference occurs, release-gate membership, and expected absence of the wrong effect. Implementation may add rows but may not delete or weaken mandatory rows without an accepted SDD amendment.

## 10. Exit criteria

### Specification phase

- documents and JSON validate;
- every requirement has an owner and at least one acceptance row;
- independent review has no unresolved Critical/High and no implementation-blocking Medium;
- branch/source custody and non-claims are explicit;
- handoff is complete;
- no runtime/source implementation exists.

### Implementation phase

- one immutable candidate passes all required T0–T3 rows;
- independent current-byte review passes;
- package/profile/readback evidence is content-addressed;
- optional T4 work is visibly unexecuted or separately evidenced; T5 execution is absent from this release authority.
