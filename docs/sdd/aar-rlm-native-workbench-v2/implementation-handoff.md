# AR-RW Implementation Handoff

**Status:** ready for implementation planning; product implementation not started
**Normative spec:** `README.md`, `CONTRACTS.md`, `LIFECYCLE.md`, `MIGRATION.md`, `migration-v6.sql`, and `contracts/contract-manifest.json`
**Defect obligations:** `defect-register.json`
**Atomic gates:** `acceptance-matrix.json`, `fault-matrix.json`, generated contracts/fixtures, and `validate_sdd.py`

## 1. Claim boundary

This handoff authorizes no source edit by itself. It tells an implementation agent how to take custody once CK authorizes implementation.

The target is one vertically complete candidate. Internal dependency ordering is allowed; no partial package is a user-visible product stage and no partial row set may be called ready.

## 2. Source custody before work

Observed baseline:

- canonical product-design worktree: repository role `canonical-development`, HEAD `dbb6b15a534ec0a7c57d6f001fffbcc0a41f8f37`;
- that worktree contains pre-existing user-owned modifications in `WAL.md` and no observed untracked files before this SDD was added;
- exact installed/released source: clean detached `v0.4.0a6`, commit `a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90`;
- AHC coordination tree contains substantial unrelated dirty work and is read-only for AR-RW;
- the installed Hermes source projection is evidence, not an implementation target.

Before editing product source:

1. record current canonical HEAD/status and hashes of this SDD package;
2. preserve the dirty canonical `WAL.md`; do not reset, clean, rebase, overwrite or attribute it to AR-RW;
3. use `a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90` as the AR-RW implementation base; changing the base requires a new source-delta review;
4. create a new clean worktree/branch from that exact base;
5. copy or commit the reviewed SDD package through ordinary repository history;
6. initialize/refresh CodeGraph only in the authorized implementation worktree, never in the installed projection;
7. keep AHC and installed AAR trees read-only.

Stop if the selected base lacks source objects needed to preserve v0.4.0a6 compatibility or if SDD bytes differ without an approved review record.

## 3. Workstream name and readiness state

Use one product workstream:

```text
AR-RW — RLM-Native IPython Workbench
```

Permitted status before full gate closure:

```text
planned
in-progress
implemented-unverified
blocked
```

`implementation_verified` requires every required T0-T3 row and fault case against one candidate manifest. `live_qualified` additionally requires separately authorized T4 evidence. Neither claim is implied by structural SDD validation.

Reference qualification matrix:

- T0/T1 contract/unit compatibility: CPython 3.11, 3.12, 3.13 and 3.14 on Linux x86_64;
- T2/T3 full process/MCP/installed-wheel journey: Linux x86_64 on the 3.11 and 3.14 bookends;
- WSL is reported as Linux and receives a separate host-environment receipt;
- native Windows and macOS are outside the AR-RW v1 product claim until separately evidenced.

## 4. Frozen architecture decisions

Implementation agents must not reopen these implicitly:

- add `aar_rlm_workbench_execute`; do not change v1 `aar_rlm_execute` semantics;
- operation-scoped IPython ownership is the v1 workbench mode;
- Python remains the orchestration language;
- typed broker facades remain the authority boundary;
- facade model/subagent methods use synchronous Python semantics backed by controlled suspension, live resume or recovery-planner continuation, not durable coroutine state;
- external model/subagent/evidence/effect-proposal work uses version-qualified generic tickets with send reservation/start, candidate receipts and reconciler settlement;
- caller-delegated admission requires `start_only=true` unless a qualified duplex host driver services the same ticket state machine;
- unresolved caller work suspends the cell and releases the attempt;
- successor attempts resume the exact surviving stack or recovery-plan a new cell from the certain pre-cell checkpoint; arbitrary Python is never automatically replayed;
- caller logical-owner/method/request-digest conflict parks instead of executing;
- deliverable artifacts are staged under cell manifests and become final only with the single FinalizationManifest CAS;
- ArtifactReference stays content-addressed; ArtifactBinding carries name/role/provenance;
- every inner write is attempt/workspace/cell fenced;
- service-managed active work gets an independent lease keeper;
- public plugin remains curated and no-code;
- AAR never owns provider credentials, child admission, privileged effect execution or final delivery;
- arbitrary live Python state remains an explicit unsupported recovery boundary.
- reference fakes remain explicit conformance backends and never satisfy configured product capability checks.

A proposed change to any item above requires an SDD amendment and independent contract/lifecycle review before source implementation proceeds.

## 5. Dependency graph — implementation order, not product stages

```text
A. Contracts, fixtures and fail-first tests
  |
  +--> B. Shared persistence hardening
  |      - DB-backed broker thread/close ownership
  |      - attempt-fenced inner mutations
  |      - monotonic journal settlement
  |      - artifact staging/commit/reconcile
  |
  +--> C. Worker protocol and typed facade
  |      - framed IPC
  |      - facade generation/grants
  |      - suspend/live-resume/recovery protocol
  |
  +--> D. Caller work and workbench waiting lifecycle
  |      - versioned model/subagent/evidence/effect tickets
  |      - send reservation/start linearization and candidate receipts
  |      - workbench phase projection, successor outbox, cancel/deadline/reconcile
  |
  +--> E. RLM workbench coordinator
  |      - planner/directive loop
  |      - operation-scoped workspace
  |      - live resume or recovery-planner continuation
  |      - result finalization
  |
  +--> F. MCP, profiles, skill, package and migrations
  |
  +--> G. One immutable full acceptance batch
```

A–D may use parallel agents only after schemas/fixtures are frozen and write ownership is disjoint. E consumes A–D. F follows stable executable contracts. G is independent and read-only against the frozen candidate.

## 6. Suggested exclusive ownership lanes

### Lane A — contracts and generated assets

Owns:

```text
src/aar/rlm_workbench_models.py
src/aar/broker_models.py
src/aar/schema_generator.py
schemas/**
fixtures/**
contract-focused tests
```

Must deliver RED fixtures first and exact canonicalization/digest behavior. Does not edit runtime implementations.

### Lane B — persistence, artifact and fencing repair

Owns:

```text
src/aar/runtime/brokers.py
src/aar/runtime/artifact_publication.py
src/aar/runtime/model_broker.py
relevant migrations
thread/race/artifact tests
```

Must repair the complete DB-backed broker family, not only `FakeArtifactBroker`.

### Lane C — IPython worker protocol

Owns:

```text
src/aar/runtime/ipython_worker.py
src/aar/runtime/ipython_backend.py
worker protocol tests
```

Must preserve stdout/stderr capture and existing v1 execute/checkpoint behavior. No broker method may bypass the supervisor/BoundBrokerFacade.

### Lane D — caller work and lifecycle

Owns:

```text
src/aar/runtime/caller_work.py
src/aar/runtime/registry.py
src/aar/runtime/dispatcher.py
src/aar/runtime/continuity.py
caller/restart/lease tests
```

Must implement the frozen caller automaton in `LIFECYCLE.md`, legacy `OperationState v1` projection, no active waiting lease, one successor outbox, cancellation and unknown outcomes.

### Lane E — RLM workbench coordinator

Owns:

```text
src/aar/runtime/rlm_workbench.py
ReferenceHost workbench integration
workbench/result tests
```

Does not copy public RLM code or embed provider/subagent clients. It consumes transport-neutral ticket/repository contracts.

### Lane F — adapter/product projection

Owns only after E stabilizes:

```text
src/aar/mcp/server.py
src/aar/mcp/models.py
profiles/**
skills/aar-operations/SKILL.md
packaging/inventory/docs
fresh-host integration probes
```

Lane F must implement the separate read-only `aar_rlm_workbench_capabilities` v8 tool. It must not add fields to the frozen v7 `aar_capabilities` response.

Generated profiles must advertise per-method configured/backend-kind/evidence-tier/reference-only truth and include a qualified real caller-work driver before claiming the journey. Do not use deprecated fixed Sampling as the new journey.

### Lane G — independent review

Read-only against one exact candidate:

- contract/API review;
- async lifecycle/temporal ownership review;
- complete defect-register reconciliation;
- immutable acceptance matrix plus candidate/evidence/results artifacts;
- package/wheel/fresh-host evidence;
- review of optional/unsupported claim boundaries.

## 7. Required first RED batch

Before product implementation, introduce fail-first cases for at least:

1. DB-backed artifact cross-thread put/read;
2. DB-backed subagent cross-thread submit/status;
3. close racing an active broker call;
4. logical artifact name preservation;
5. failed-cell artifact non-publication;
6. multi-artifact atomic commit;
7. RlmJobSpec v2/workbench schema validation;
8. one-job operation-scoped workspace creation;
9. worker broker frame validation;
10. model-call suspension with live resume and lost-stack recovery continuation;
11. caller-work logical-owner/request-digest conflict;
12. workbench `waiting_external` phase without active lease and with frozen operation-state projection;
13. long service-managed call with lease keeper;
14. late model-journal writer after quarantine;
15. stale-attempt inner broker/RLM write rejection;
16. subagent retained-handle lifecycle;
17. terminal result with output/artifacts/usage/child/workspace disposition;
18. v1/public compatibility fixtures.
19. planner response-contract validation before directive/code admission;
20. profile capability truth for reference-only, partial and fully configured hosts;
21. caller-delegated blocking/start-only deadlock rejection;
22. workbench job submission idempotency/conflict;
23. provisional progress/display resume/recovery deduplication.
24. crash before and after the send-start linearization point;
25. candidate receipt append versus authoritative reconciliation;
26. finalization proposal/cancellation/CAS races;
27. normative matrix versus hash-bound acceptance-results claim validation.

A test that passes before implementation is not a valid fail-first proof unless it establishes an already-correct prerequisite.

## 8. Implementation details that must remain explicit

### 8.1 Broker repository strategy

Use a repository-owned connection factory with one short-lived SQLite connection per transaction, WAL mode, foreign keys enabled, a 5,000 ms busy timeout, bounded retry for `SQLITE_BUSY`, and explicit `BEGIN IMMEDIATE` for writes. No SQLite connection crosses threads. Runtime close fences new transactions and waits for active transactions before closing the factory. Do not introduce a second shared-connection strategy.

Required soak: at least 32 concurrent workers, 10,000 mixed artifact/subagent/caller-journal operations, injected busy/rollback/close races, zero corruption, zero thread-affinity failures and deterministic idempotency counts.

### 8.2 Artifact publication strategy

Use staged rows plus `CellCommitManifest` and idempotent reconciliation. Define visibility and garbage collection for abandoned staged bytes. Preserve diagnostic artifacts only under an explicit role/API.

### 8.3 Worker transport

The existing line protocol must be versioned rather than implicitly extended. Bound frame sequences/bytes, keep user output off the control channel, and test worker/supervisor version mismatch.

### 8.4 Suspend/resume/recovery

Persist before execution:

- pre-cell checkpoint binding;
- cell source bytes/digest;
- cell execution ID;
- route/grant/context digests;
- cumulative budget snapshot.

If the same write-fenced worker survives, the successor may resume its stack. If the stack is lost, restore the pre-cell checkpoint, record `lost_before_commit`, and use a bounded recovery-planner call to create a new continuation cell. Never automatically replay arbitrary Python and never attempt generic Python stack serialization.

### 8.5 Caller work

Model, retained subagent, evidence and effect-proposal tickets share lifecycle machinery but retain version-qualified closed request/observation schemas. Claim reserves a pre-send physical attempt; `mark_send_started` is the conservative may-have-sent point; callbacks append candidate receipts; the current reconciler settles. Caller-delegated jobs require `start_only=true`; service-managed blocking requires an injected configured broker and independent lease keeper.

### 8.6 Result finalization

Result success is a reconciliation gate, not merely `aar.complete()` being called. It requires:

- no unresolved required tickets;
- output schema pass;
- artifact commit manifests pass;
- cumulative budgets consistent;
- current workbench control/finalization revision;
- workspace terminal disposition recorded.

## 9. Compatibility obligations

- preserve v1 RLM schema/tool bytes and tests;
- preserve public caller-delegated v1 semantics and no-code tool catalog;
- preserve plain/IPython workspace direct operations;
- preserve AR-LT operation/recovery evidence or version changes additively;
- preserve AR-MB route/receipt/usage/no-fallback semantics;
- preserve host authority and unsupported security-sandbox projection;
- regenerate schemas, fixtures, profiles, skill digests, inventories and package assets together;
- require fresh supported-host probes after package install/restart/new session.

## 10. Verification commands and evidence order

Use repository-declared commands from the selected implementation base. At minimum, the owning repo currently demonstrates these command families:

```text
uv run pytest <focused tests>
uv run ruff check src tests
uv run pytest
uv run aar-contract verify
uv run aar-mcp-assets verify
uv run aar-host-assets verify
package build and exact-wheel install/readback
fresh MCP/host profile probes
```

Do not copy command arguments from this handoff without confirming the current CLI/help in the implementation base.

Evidence order:

1. focused RED/GREEN;
2. complete relevant unit/integration suites;
3. schema/fixture/asset verification;
4. migration/rollback copies;
5. exact wheel and installed-to-source inventory;
6. full MCP attached-supervisor fault matrix;
7. fresh Hermes/Codex host journey;
8. separately authorized T4 route/child qualification;
9. independent immutable-candidate review.

## 11. Stop rules

Stop and return to design if:

- a broker call requires provider credentials in the worker;
- a caller wait requires holding a dispatcher lease as the only correctness mechanism;
- arbitrary Python stack/pickle becomes a recovery dependency;
- failed/suspended work publishes final artifacts;
- `check_same_thread=False` is used without concurrency/close ownership;
- a late/stale writer can mutate terminal or inner durable state;
- caller-ticket replay can cause a second physical model/child/effect execution;
- public plugin gains arbitrary code unintentionally;
- v1 semantics drift without a new version;
- AHC types or final-delivery fields enter portable AAR schemas;
- implementation is proposed directly in the installed source projection;
- source/SDD/candidate custody becomes indeterminate.

## 12. Expected implementation review packet

The implementation agent must return:

- exact branch/worktree/base/candidate identities;
- source diff and CodeGraph impact summary;
- schema/fixture/profile/skill digest changes;
- defect-register mapping: repaired, intentionally unsupported, or still blocked;
- acceptance-matrix result for every row;
- migration/rollback receipts;
- package/wheel/fresh-host evidence;
- provider/child authorization and evidence tier for T4, or explicit pending status;
- unchanged unrelated dirty-work proof;
- no readiness claim beyond executed evidence.
