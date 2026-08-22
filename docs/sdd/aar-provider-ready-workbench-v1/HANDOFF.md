# AR-PRW Specification Handoff

## 1. Status

- Product lane: **AR-PRW — Provider-Ready RLM Workbench**
- Provisional package target: **`0.6.0a0`**
- Specification phase: **S0 successor candidate; independent current-byte review pending**
- Implementation authorization: **authorized by CK for the dedicated clean successor branch; writer gate remains held until S0 freeze**
- Product source implementation: **not started; writer Baton gate held**
- Exact-base verification: **`743 passed, 5 skipped, 1 warning`; Ruff PASS**
- Real runtime migration, activation, provider qualification and paired benchmark: **not performed**

## 2. Source custody

The exact executable behavior baseline is clean `adaptive-agent-runtime 0.5.0a0`, commit `bd30df40a9f3e77bcf2d244dbf4fd9bba0148ba1`. The active SDD successor lives on branch `codex/aar-provider-ready-workbench-v1-impl`, rooted at that commit. Product source and tests remain unchanged.

The historical reviewed SDD generation is retained outside the public repository and bound by complete custody-tree digest `sha256:bb0287e1718033c773e763096b202591307458e1524ab8de9ea8d5a29039afee`, semantic validator digest `sha256:30dbc6ffb52a289792d7b37d782ce8b537fd0741957af9854b9538a863a01047`, and receipt file SHA-256 `9243d6f8306c774ba0ed752eec0a4121019b6a36edf8a4bb0799423b465b6418`. Machine-local paths are intentionally absent from this public handoff.

## 3. Chosen architecture

1. Keep the MCP v8 38-tool surface and exact frozen v7/v8 schemas.
2. Reuse registry-v6 DDL, AR-MB route/receipt records, caller-work tickets, supervisor, and existing authority contexts.
3. Add one `aar-admin` operator surface for stdout-only zero-write/zero-checkpoint planning/status/verification and explicit cutover, abort, exact-input reconcile, empty-runtime v5 bootstrap, pre-frontier restore, and activation mutations.
4. Use a strict, secret-free activation intent; both preserved and bootstrapped-empty v5 roots enter the same truthful snapshot-bound v6 cutover and generate the distinct final host profile.
5. Make caller-delegated root planner calls durable `model.request` tickets; never call the synchronous planner in that mode.
6. Normalize job/profile planner mode, resolve the complete coherent `grant_ids` set, then derive required methods from budgets/features, effective capabilities and matching factories.
7. Publish bounded workbench grants only from strict server-side grant-set and issued records bound to the active supervisor/profile/history/capability/route generation; client context never self-authorizes.
8. Qualify AAR and Prime on Luna/max separately; an optional strict unexpired paired planning document may bind exact qualification/evaluation bytes and operator planning authority, but v1 defines no T5 physical-launch authority and infers no missing Prime telemetry.

## 4. S0 successor decision

Generation-5 fixed-byte dual review verified exact custody but returned a batch-complete S0 block and an incomplete A1a packet. This successor generation closes the marker-cycle/schema, initialization-authority, mode-normalization, planner-outcome, grant-array/availability, readback/deadline, paired-admission, validator-coverage and packet-shape findings without product-source changes, and now owns:

- fourteen complete strict schema owners with required properties, nullability, bounds, cross-field rules and self-digest domains, including acyclic prepared/receipt/terminal construction and paired admission; the impossible separate fresh-v6 receipt is retired;
- strict lexical aliases and no-normalization semantics for every A1a string field;
- a `1..6` unique adapter subset in the exact frozen v8 broker-catalog order; generic `artifact.read` is excluded from activation, and `artifact.put + caller_driver` rejects because frozen v6 caller-work has no such owner;
- `1..64` sorted unique route IDs, exact principal IDs, and capabilities;
- exact-match-only v1 semantics for the legacy `principal_patterns` field;
- positive-counter wire validation plus append-only per-profile authority history as the generation commit/non-reuse owner; `current.json` is a derived pointer, and cold start detects visible fork/partial-deletion/pointer-rollback against history plus immutable markers; complete mutually consistent offline rollback requires a future external authority;
- canonical package versions with no leading-zero aliases;
- frozen v8 reference truth (`configured=true`, `reference_only=true`, `evidence_tier=unknown`) separated from admission usability;
- frozen capability evidence tier separated from evaluator-owned component-wise provider/model/reasoning/fallback/cache plus usage/visibility classification; Prime mixed observed/requested-only treatment is representable without inventing effective effort;
- semantic schema/import/expansion/execution security checks rather than identifier-content guessing;
- exact frozen planner logical-owner wire `{kind, phase, step_index}` with a unique predecessor event, fenced `prepared→prepared` takeover, and atomic one-time valid-directive/correction/certain-failure/cancel/deadline projections; outcome unknown stays unconsumed and planner never touches cell-bound authority/rebind rows;
- explicit `cutover/restore reconcile` and cutover `abort/apply` commands with exact write sets; runtime initialize creates no independent epoch/reconcile namespace, while read-only plan is stdout-only and never checkpoints WAL;
- one strict mode-tagged planner object with exact `caller_delegated→caller_delegated_ticketed` and `service_managed→service_managed` normalization, package-owned factories, complete sorted-unique `grant_ids` resolution, truthful readback and mandatory admission cross-products;
- incident-shaped assertions preventing waiting snapshots from becoming success, possibly-sent deadline states from becoming certain, and absent usage from becoming zero;
- independent self-digest recomputation and nested inner/outer tamper sequencing.

This successor must validate deterministically and receive independent current-byte review before it advances authority.

## 5. Deliberate non-additions

No new daemon, state database, provider credential store, embedded provider SDK, MCP inference back-channel, MCP tool, generic plugin ABI, principal glob/regex engine, or silent startup migration is proposed.

## 6. Remaining gates

Before product implementation:

1. successor structural validator PASS on two deterministic generations;
2. public-release hygiene PASS;
3. independent fixed-byte S0 review with no blocker;
4. regenerated current-byte A1a packet independent PASS;
5. exact task-start commit and exclusive two-path Luna/max dispatch.

Before live qualification or evaluation:

- immutable implementation candidate passing all mandatory T0–T3 gates;
- separate provider-call authority;
- exact provider-alias, route, receipt, usage, stop, and budget manifests;
- separately authorized T4 qualification may establish each arm's evidence tier before an optional paired-planning document; T5 physical execution remains not authorized.
- an optional unexpired strict paired-planning document may bind both qualifications, exact bytes/budgets/stop matrix, operator planning authority and arm order, but it contains no attempt IDs and grants no T5 send authority.

## 7. Artifact map

Core documents:

- `README.md`
- `BASELINE.md`
- `ARCHITECTURE.md`
- `CONTRACTS.md`
- `LIFECYCLE.md`
- `MIGRATION.md`
- `EVALUATION.md`
- `ACCEPTANCE.md`
- `IMPLEMENTATION-PLAN.md`
- `HANDOFF.md`
- `decisions/ADR-001-activate-existing-authorities.md`
- `decisions/ADR-002-ticket-root-planner.md`
- `decisions/ADR-003-preserve-v8-method-scoped-admission.md`
- `decisions/ADR-004-freeze-activation-contract-domains.md`

Machine-readable planning:

- `verification/requirements.json`
- `verification/acceptance-matrix.json`
- `verification/validate_spec.py`
- `verification/spec-validation-receipt.json`

Historical review files remain evidence for their exact older generation only. A new current-byte S0 review is required.

## 8. Release sequencing

No intermediate phase installation, push, tag, publication, or release is allowed. After every non-live implementation phase and mandatory exact-candidate gate converges, PMO installs the completed candidate, directs Luna/max installed-candidate verification, updates final docs, verifies the configured `phenomenoner/adaptive-agent-harness` remote, then pushes, tags, and creates the GitHub release. T4/T5 and production cutover remain separately authorized effects.

## 9. Next authorized action

PMO may validate, review and freeze this S0 specification successor. Product-source writers remain blocked until that review and the corrected A1a packet both pass.
