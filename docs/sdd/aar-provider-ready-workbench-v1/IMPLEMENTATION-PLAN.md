# AR-PRW Implementation Plan

**Authority state:** product implementation is authorized on the dedicated clean successor branch, but source dispatch is held until the S0 specification successor and first worker packet independently pass review.

## 1. Entry condition

Implementation begins only after:

1. CK explicitly authorizes implementation;
2. this SDD is reviewed/frozen or amended;
3. a clean worktree is created from exact accepted v0.5-or-later source;
4. local dirty work remains preserved;
5. baseline tests and source-delta review are recorded.

This plan is sequencing guidance, not authority to edit source, migrate a real runtime, call a provider, publish, commit, or push.

## 2. RED-first lanes

### Lane A — Frozen contracts and generators

Likely ownership:

- fourteen canonical model/schema owners, including strict prepared/terminal markers, append-only activation-generation authority, server grant-set, component-wise evaluator classification and paired-admission records;
- `schemas/` generated documents;
- valid/invalid fixtures;
- package asset parity tests.

RED first:

- activation intent/final-profile unknown fields, secrets, shell interpolation, self/nested digest cycles, bad digest equality, non-canonical package versions, forbidden seventh workbench method, and generation wire/CAS cases;
- complete property/nullability/bound/cross-field matrices for cutover/restore/inner-outer-marker/readback/grant/evaluation/paired-admission records;
- empty-runtime v5-bootstrap→ordinary-cutover and restore receipt omission/frontier/sidecar errors;
- provider-alias attestation and evaluator-classification tamper/requested/effective overclaim;
- cross-validator/canonical digest disagreement;
- changed frozen v7/v8 bytes.

Exit: T0 contract rows pass; no runtime behavior claim.

### Lane B — Operator cutover

Likely ownership:

- new `aar-admin` entry point;
- public wrapper around reviewed migration-v6 primitives;
- local file authority, acyclic final-profile generation, append-only per-profile activation-history CAS plus derived-current repair, stdout-only plan, snapshot/cutover/empty-runtime-v5-bootstrap/reconcile/restore helpers;
- focused migration/fault tests.

RED first:

- plan mutation canary;
- active/replaced owner and shared-supervisor/exclusive-cutover lock overlap;
- DB/WAL/SHM drift, backup/restore fsync and sidecar disposition;
- crash after snapshot-before-prepared adoption/conflict and at prepared, transaction, DB commit, final-profile, immutable history publication, derived-current replace, restore replacement and terminal-marker boundaries;
- exact-input reconcile versus changed input, implicit apply/abort rejection, and plan zero-checkpoint canaries;
- absent/first, equal, lower, strict-higher, stale-prior and ABA generation cases;
- exact rerun vs changed-byte conflict;
- restore after frontier.

Exit: T1 cutover rows pass on disposable databases.

### Lane C — Activation composition and grants

Likely ownership:

- activation intent/final-profile loader, generator, and verifier plus complete history/marker scan and derived-current readback;
- supervisor construction path;
- reference host capability projection, strict server grant-set and complete sorted-unique `grant_ids` resolver;
- operator status/readback;
- Hermes profile/package assets.

RED first:

- no final profile => no grant/unconfigured methods;
- intent/legacy-attestation/final-profile digest cycle or mismatch => reject;
- profile boolean or arbitrary import without package-registered factory => reject; caller and service-managed profiles each resolve the exact bound caller-driver/native `model.request` manifest from the same registry and project its factory/digest in planner readback; exact reference factory projects configured/reference-only/unknown but remains admission-unusable;
- stale migration/profile/factory/adapter/route/grant/recovery digest;
- Ready/grant issuer ordering, exact multi-record set/principal/session/profile/history/capability/route/budget binding, duplicate-capability/mixed-set rejection, TTL/deadline expiry, runtime-generation revocation and ABA;
- credential canary.

Exit: activated no-inference host reports truthful per-method rows and scoped grants.

### Lane D — Durable root planner

Likely ownership:

- workbench coordinator;
- caller-work logical owner/request/settlement integration;
- scheduler/recovery hooks;
- planner/correction/recovery/finalization tests.

RED first:

- caller-delegated initial planner ticket, `start_only` rule, and fail-if-called synchronous planner canary;
- strict v6 logical-owner/request/suspension/receipt map, unique predecessor event, null-cell outbox pending prepare, dead-owner generation-advancing prepared takeover, and atomic consume of valid directive/correction/certain failure/cancel/deadline, while outcome unknown stays unconsumed, with zero planner access to cell-bound authority/rebind tables;
- attempt lease released while waiting;
- crash before/after send-start and settlement;
- stale/duplicate successor;
- correction usage charge;
- recovery planner repeated failed source;
- invalid-directive correction/exhaustion plus cancellation/deadline/outcome-unknown/finalization races.

Exit: caller-delegated workbench can complete with deterministic fake caller receipts; no provider call.

### Lane E — Method-scoped admission

Likely ownership:

- workbench capability/admission models;
- required-method derivation;
- facade capability projection;
- partial/full profile tests.

RED first:

- separately activated caller-driver/native `model.request` planner profiles × exact `caller_delegated→caller_delegated_ticketed`/`service_managed→service_managed` normalization × matching/mismatching job mode × artifact/subagent budget × optional multi-grant set × factory-kind cross-product;
- model-only job on a truthful partial profile;
- feature-implied but ungranted/retired-factory authority denial vs valid-current-grant same-generation adapter-health unavailability;
- optional unavailable invocation before send;
- reference/stale adapter rows;
- grant-required methods.

Exit: all mandatory admission cross-product rows are release-gate green; partial capability truth is usable without false full-host claims.

### Lane F — Installed host/product assets

Likely ownership:

- `pyproject.toml` script declaration;
- bundled Hermes profile and operation skill;
- release/status/host compatibility documents;
- exact-wheel preserved-v5 and empty-runtime-v5-bootstrap host scripts.

RED first:

- source/wheel/profile/skill copy drift;
- default/unactivated profile accidentally authorizes inference;
- live host session still sees stale catalog/profile;
- source import leakage.

Exit: exact wheel passes T3 preserved-v5 and empty-runtime-v5-bootstrap roots.

### Lane G — Optional live qualification and offline paired planning

Separate authorization. A later CK decision may permit bounded T4 route qualification only. The implementation may then materialize strict evaluator classifications and the offline paired-planning document, but v1 has no T5 launcher, paired attempt, spend, score, retry, or expansion lane. A future launcher-authority SDD is a separate project decision and cannot be inferred from this plan.

## 3. Dependency order

```text
A contracts
  -> B cutover
  -> C activation/grants
  -> D durable planner
  -> E method admission
  -> F exact-wheel host qualification
  -> independent current-byte review
  -> candidate freeze
  -> optional G live qualification / offline planning evidence only
```

B and D may be developed in isolated worktrees after A freezes, but shared registry/contracts/schemas are single-owner. C depends on B's receipt contracts. E depends on C/D semantics. F begins only after executable bytes stabilize.

## 4. Test discipline

- Write the smallest failing seam test before production change.
- Record the RED failure reason; a syntax/import failure is not a behavioral RED.
- Make one narrow change to GREEN.
- Refactor only under green focused tests.
- Re-run affected predecessor compatibility rows.
- Defer full suite/matrix until candidate bytes stabilize.
- Final claims use exact wheel/disposable roots, not editable checkout convenience.

## 5. Review checkpoints

1. **Contract freeze:** schemas/fixtures only.
2. **Cutover freeze:** migration/authority fault matrix.
3. **Runtime freeze:** planner/admission/grants lifecycle.
4. **Packaging freeze:** exact bundled bytes.
5. **Independent complete review:** current candidate, no writer self-review substitution.
6. **Release candidate freeze:** immutable digests.
7. **Optional T4 authority:** live qualification.

At each checkpoint, update owning WAL/evidence and roadmap only when durable status changes.

## 6. Commit boundaries

Recommended commits after implementation authority:

1. contract schemas/fixtures;
2. `aar-admin` and migration tests;
3. activation/profile/grant composition;
4. root-planner caller tickets;
5. method-scoped admission;
6. package/profile/skill/docs;
7. verification receipts/status.

Stage intended files only. Never bundle unrelated dirty branch work. No push, PR, package publish, release, live inference, or runtime migration without separate authority.

## 7. Expected artifacts

```text
schemas/
tests/fixtures/
src/aar/... operator and activation modules
profiles/hermes/...
skills/aar-operations/...
tests/... focused and compatibility
release / host compatibility receipts
```

Exact names may change after source-level navigation, but owners, contracts, gates, and non-claims may not drift silently.

## 8. Definition of implementation complete

Not “code exists.” Complete means one immutable candidate passes all mandatory T0–T3 rows, exact-wheel provenance and live-host pickup reconcile, independent review is clean at the accepted severity threshold, and T4/T5 remain either separately evidenced or explicitly unexecuted.
