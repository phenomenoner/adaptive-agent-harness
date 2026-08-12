# Durable Long-Operation Continuity Plan

**Status:** AR-LT0 through AR-LT3 verified and installed; schema v5, runtime/dispatcher generation 13
**Date:** 2026-08-12
**Scope:** standalone AAR core, runtime supervisor, MCP/direct adapters, RLM, and programmable workspace
**Acceptance boundary:** this document does not reopen or expand the verified AR-0 through AR-3 candidates

## 1. Decision summary

AAR is adding durable long-operation continuity as an independent `AR-LT` workstream before treating
a general-purpose worker runtime as the default answer to every long task. The first standalone
contract, durable-RLM, supervisor-separation, policy-bound RLM successor, workspace restore, and
general effect-aware continuation slices are source verified and installed behind the host-managed
supervisor on schema v5 at runtime/dispatcher generation 13.

The first useful target is deliberately narrower than a distributed workflow engine:

- an accepted operation outlives one MCP request and one client connection;
- client disconnect is not implicit cancellation;
- a stable operation identity can be queried from a newly authorized connection;
- accepted work is claimed through a durable lease and survives runtime restart;
- event, result, artifact, and checkpoint references are persisted and cursor-readable;
- interrupted running work is reconciled before replay;
- replay-safe or checkpoint-resumable work continues through a successor attempt;
- ambiguous broker/effect calls remain indeterminate rather than being blindly replayed.

The design remains transport-neutral. MCP is one adapter over the same operation and recovery contracts used by the direct SDK and a future native host adapter.

### 1.1 Phase-level implementation handoffs

The architecture in this document is decomposed into four implementation-ready phase plans:

- [AR-LT0 — Continuity Contract and Failure Model](AR-LT0-CONTRACT-AND-FAILURE-MODEL-PLAN.md) — schemas, SQLite migration, attempts, leases, cursored events, authority, fixtures, and compatibility.
- [AR-LT1 — Durable Async Dispatch and Reconnect](AR-LT1-DURABLE-ASYNC-DISPATCH-PLAN.md) — dispatcher, atomic claims, durable RLM handler, reconnect, cancel, crash matrix, and rollback.
- [AR-LT2 — Durable Supervisor and Restart Recovery](AR-LT2-DURABLE-SUPERVISOR-PLAN.md) — persistent supervisor, ephemeral adapters, private IPC, exact process identity, worker ownership, and restart recovery.
- [AR-LT3 — Checkpoint- and Effect-Aware Continuation](AR-LT3-CHECKPOINT-AND-EFFECT-RECOVERY-PLAN.md) — RLM step-boundary successors, new-generation workspace restore, broker/effect reconciliation, and explicit unsupported state.

Each plan identifies exact source/test impact, ordered work packages, failure injection, exit evidence,
rollback, and the AHC coordination boundary. AR-LT0 through AR-LT3 now contain source and installed
closeout evidence. The installed claim remains bounded to standalone AAR and does not imply AHC
activation or external-effect authority.

The cross-project mapping is maintained in `AAR-LONG-TASK-CONTINUITY-COORDINATION-PLAN.md` in the separate AHC-by-AAR planning workspace. That planning document is not normative to AAR core. AR-LT adds no new shared gate and does not block HC-R0.

## 2. Why this is a bounded opportunity

AAR already owns most of the hard truth-preservation primitives:

- [`OperationRegistry`](../src/aar/runtime/registry.py) persists accepted intent, scoped idempotency, runtime generation, record revision, terminal result/failure, certainty, reconciliation state, and operation events in SQLite/WAL.
- `start_runtime()` rebinds accepted/no-effect intent to a new generation and classifies interrupted running work as indeterminate.
- [`RlmStore`](../src/aar/runtime/rlm.py) persists RLM jobs, steps, and terminal result separately from outer operation truth.
- [`BrokerJournal`](../src/aar/runtime/brokers.py) persists a broker call as `started` before invocation and records the authoritative response only after success. A replay of a started call without a receipt becomes `BrokerCallIndeterminate`.
- [`SupervisedIPythonWorkspaceBackend`](../src/aar/runtime/ipython_backend.py) already exposes interrupt, health, reconcile, JSON-subset checkpoint, restore, worker-loss classification, generation, and revision fencing.
- The public MCP surface already exposes stable operation handles, status, cancel, reconcile, artifact references, and `start_only` on scalar workspace and RLM execution.

AR-LT0/AR-LT1 close the first execution-ownership slice:

- durable `rlm.execute` submissions enqueue and dispatch independently of the MCP request lifetime;
- atomic attempts, leases, heartbeats, recovery decisions, cancellation control, and event pages are
  persisted in SQLite;
- a new single-runtime generation recovers safe work through a fenced successor attempt and reuses
  authoritative broker receipts;
- unresolved broker calls remain indeterminate rather than being replayed;
- a database-scoped OS ownership lock rejects a second live reference-host process before it can
  steal the runtime generation.

AR-LT2 moves authoritative worker bindings and receipts into the registry, places the dispatcher and
workspace backend under one durable supervisor, and reduces `aar-mcp` to an authenticated ephemeral
frontend. AR-LT3 now verifies installed policy-bound RLM continuation from certain receipt-backed step boundaries.
Portable workspace checkpoint restoration and general effect-aware continuation remain open claims.

## 3. Fault boundaries

Continuity claims must name the boundary they cover.

| Boundary | Required behavior |
|---|---|
| MCP request cancellation or socket loss while AAR runtime remains alive | Operation continues unless explicitly cancelled; a new authorized read retrieves status/events/result. |
| Ephemeral `aar-mcp` frontend exits while a durable runtime supervisor remains alive | Frontend can reconnect; worker ownership and operation state are unchanged. |
| AAR runtime supervisor restarts before an accepted operation is claimed | Accepted intent is rebound, leased, and executed once. |
| Supervisor or worker exits during a running operation | Old attempt is fenced; authoritative receipts are reconciled; replay-safe/checkpoint policy determines successor, park, or failure. |
| Programmable worker exits after a portable checkpoint | Restore the checkpoint into a new workspace generation and create a successor attempt. |
| Broker/effect call is `started` without a terminal receipt | Do not replay automatically; park as indeterminate until the host/provider reconciles it. |
| Host reboot or cross-host migration | Deferred unless a separately accepted service/deployment profile owns startup, placement, storage, and artifact availability. |

## 4. Non-goals

`AR-LT` must not claim or introduce:

- transparent resurrection of arbitrary Python process memory;
- portable pickle checkpoints;
- exactly-once external effects as a generic guarantee;
- AAR-owned provider credentials, host admission, source closure, outbox, or final delivery;
- Hermes prompt/session internals, Codex thread IDs, AHC channel identities, or AHC memory ownership in the AAR core schema;
- a multi-tenant security sandbox;
- cross-host workflow DAG scheduling in the first continuity release;
- implicit replay of an indeterminate broker call;
- implicit cancellation when an MCP connection disappears.

## 5. Target topology

```text
MCP stdio / MCP HTTP / direct SDK / native host adapter
                         |
                  transport adapters
             ephemeral; no job ownership
                         |
               private local IPC/socket
                         |
              durable AAR supervisor
     registry + dispatcher + leases + event log
                         |
        +----------------+----------------+
        |                                 |
   bounded RLM workers          programmable workers
        |                                 |
 broker receipt journal        checkpoints + artifacts
        +----------------+----------------+
                         |
                 host-owned brokers
```

A deployment may initially combine adapter and supervisor in one process for a bounded profile. The contract must still separate their roles so an ephemeral stdio frontend can later attach to a persistent supervisor without changing operation semantics.

## 6. Transport-neutral continuity contract

### 6.1 Identity model

- `operation_id`: stable logical job identity.
- `attempt_id`: immutable identity for one execution attempt.
- `runtime_generation`: supervisor fencing generation.
- `worker_generation`: worker/process fencing generation.
- `record_revision`: monotonic operation record revision.
- `event_sequence`: monotonic cursor across operation events.
- `checkpoint_ref`: digest-bound manifest/artifact reference.
- `predecessor_attempt`: immutable lineage pointer for a successor attempt.

A restart or checkpoint resume creates a successor attempt. It must not rewrite an ambiguous predecessor into a success or erase its broker/effect trace.

### 6.2 Recovery policy

Each executable operation binds an explicit policy such as:

- `never`: no automatic replay or checkpoint resume;
- `replay_safe`: the operation can start again only when prior authoritative effects are absent or reconciled;
- `checkpoint`: a successor may restore the latest compatible checkpoint;
- `host_decides`: park and request an authoritative host recovery decision.

Policy is input-bound and digest-bound. Runtime inference cannot silently upgrade it.

### 6.3 Reconnect authorization

An operation ID is not authorization.

The current reference MCP read path requires exact principal and session binding. Cross-connection recovery therefore needs an explicit, host-issued continuity authority without weakening session isolation. Candidate shapes:

1. the host preserves the original logical session reference across transport reconnect;
2. the submit result returns an opaque continuity grant bound to operation, principal, allowed methods, expiry, and capability digest;
3. a native host adapter translates a stronger host identity into the original AAR operation scope.

The selected shape must reject foreign principals, changed capability digests, stale generations, and unrelated sessions. No bearer secret is written into public artifacts or logs.

### 6.4 Public operations

The minimal core surface is:

```text
submit / accept -> operation handle
status(operation)
events(operation, after_sequence, limit)
cancel(operation)
reconcile(operation)
recover(operation, expected_revision, policy decision) -> successor attempt or parked result
checkpoint / artifact references
```

MCP clients with a verified standard task mechanism may receive a task projection. Older clients continue to use operation handles and polling/event cursors. Core semantics do not depend on either MCP era.

### 6.5 Versioning

Changing `start_only` from accept-only to durable enqueue is a behavior change and requires:

- an explicit tool-surface version change;
- operation-skill version/digest change;
- updated generated schemas/descriptions;
- compatibility fixtures for old and new semantics;
- fresh host rows before claiming Codex or Hermes support.

Do not silently add optional fields to strict v1 records if that changes canonical fixture bytes or validation meaning. Prefer separate versioned attempt/event-page/recovery models when required.

## 7. Persistence design

Candidate additive tables:

```text
operation_attempts
  operation_id
  attempt_id
  predecessor_attempt_id
  state
  recovery_policy
  runtime_generation
  worker_generation
  started_at / heartbeat_at / terminal_at
  checkpoint_digest
  failure / result digest

operation_leases
  operation_id
  attempt_id
  lease_token_digest
  lease_owner
  lease_generation
  lease_expires_at

operation_checkpoints
  operation_id
  attempt_id
  checkpoint_digest
  manifest_json or artifact reference
  environment_digest
  created_at

operation_event_payloads or a versioned event table
  sequence
  operation_id / attempt_id
  event kind
  bounded payload or artifact reference
```

The existing operation record remains the logical aggregate and terminal truth. Attempt and lease rows are execution-control records, not alternate operation truth.

Requirements:

- WAL mode and explicit transaction boundaries;
- accepted intent committed before acknowledgement;
- lease claim uses compare-and-swap on expected state/revision;
- lease expiry never by itself means the prior effect failed;
- terminal result and authoritative receipt binding commit before delivery/readback claims;
- large events and outputs use content-addressed artifacts;
- event queries are paged and bounded;
- retention and disclosure limits are capability-visible.

## 8. Dispatcher and restart algorithm

### 8.1 Accepted operation

1. Load an accepted operation whose deadline has not expired.
2. Verify capability digest, grants, budgets, input digest, and recovery policy.
3. Atomically create/claim an attempt and lease under the current runtime generation.
4. Emit a durable `attempt_claimed` event.
5. Start the worker only after claim commit.
6. Refresh heartbeat/progress under the same fenced lease.
7. Commit terminal receipt/result and then release the lease.

### 8.2 Supervisor restart

1. Increment runtime generation.
2. Rebind unclaimed accepted operations.
3. Fence old running attempts.
4. Read broker, workspace, and worker receipts.
5. Classify each attempt as completed, indeterminate, replay-safe, checkpoint-resumable, or unrecoverable.
6. Create a successor attempt only after the classification and policy permit it.
7. Reject every late result from an old runtime/worker/lease generation.

### 8.3 Cancellation

- cancellation is an explicit operation, not a transport side effect;
- cancel accepted work before claim without starting a worker;
- cancel running work cooperatively first, then terminate under a bounded deadline;
- reconcile admitted children, broker calls, and workspace effects after termination;
- terminal success that committed before a cancellation race remains authoritative;
- cancellation status and source are durable events.

## 9. RLM resume rules

The current RLM store and broker journal make step-boundary resume feasible.

A successor may continue at step `N + 1` only when:

- steps `0..N` are present and content-bound;
- their broker receipts are authoritative;
- the strategy/spec/grant/capability/budget identities match;
- the next request is deterministically derived or bound by a stored digest;
- no prior broker row for the next step is left in `started` without a receipt.

If a broker call is `started` without a terminal receipt, the RLM operation parks as indeterminate. Reconciliation or a host-authorized compensation/recovery decision is required. Blind provider retry is forbidden.

Usage is reconstructed from authoritative succeeded broker rows. A successor must not double-count predecessor usage or reset the original admitted budget.

## 10. Programmable-workspace resume rules

The portable boundary remains `aar.workspace-checkpoint.v1` and its explicit JSON subset.

- A successful checkpoint binds operation, attempt, source handle, environment fingerprint, values, exclusions, artifacts, and digest.
- A worker restart restores into a new workspace generation at revision zero.
- The old workspace generation remains immutable/lost/closed.
- Auto-checkpoint may run after a successful cell or at an explicit yield, not by serializing an in-flight interpreter.
- Unsupported values remain exclusions; the runtime cannot imply they were restored.
- A failed or killed cell resumes from the last accepted checkpoint, not from an arbitrary Python instruction.
- File descriptors, sockets, subprocesses, modules, native extension state, NumPy arrays, and pandas objects are not portable unless converted into the declared subset or artifact form.

## 11. Relationship to AHC

AHC HC-R0 design and current source contain useful generic patterns:

- durable accepted intent;
- worker resume leases and expiry/reclaim;
- backend recovery identity;
- binding/process/turn generation fencing;
- progress cursor and checkpoint;
- native resume versus explicit successor binding;
- needs-user/park/indeterminate recovery decisions;
- stale-generation rejection.

AAR may reuse the concepts and compatible fixtures, but not AHC authority types. In particular, AAR core must not own:

- AHC source/channel/agent routing identity;
- virtual-session ownership;
- OpenClaw-Mem ownership;
- provider delivery or outbox;
- live deployment/cutover authority.

HC-R0 is not an accepted AAR continuity implementation. Its separately governed progress remains
design and future adapter input until the applicable shared gates pass.

## 12. Work packages

### AR-LT0 — Contract and failure-model spike

**Effort class:** S; estimated 3–5 engineering days.
**State:** verified at the standalone contract/migration boundary.

Deliverables:

- versioned attempt, lease, event-page, checkpoint-binding, continuity-authority, and recovery-decision models;
- valid/invalid fixtures and canonical digests;
- explicit compatibility strategy for current strict models;
- fault-boundary matrix and restart state machine;
- no runtime behavior claim.

Exit gate:

- T0 schema/fixture validation;
- property/model checks reject stale lease, foreign continuity scope, late generation, conflicting idempotency, unbounded event requests, and ambiguous replay.

### AR-LT1 — Reconnectable durable async

**Effort class:** M; estimated 2–4 engineering weeks after LT0 stabilizes.
**State:** verified for durable `rlm.execute` in the standalone single-runtime reference host.

Deliverables:

- durable dispatcher and lease claimant;
- real enqueue semantics for supported `start_only` operations;
- accepted-operation startup pickup;
- paged event cursor;
- durable status/result/artifact retention;
- explicit cancellation independent of connection lifetime;
- scalar workspace and replay-safe RLM vertical slices.

Exit gate:

- T1/T2 lifecycle checks and T3 process-loss scenarios prove client disconnect, duplicate submit, server restart, claim race, cancellation race, stale result rejection, and exact readback.

### AR-LT2 — Ephemeral frontend and durable supervisor

**Effort class:** M/L; estimated 3–6 additional engineering weeks.
**State:** verified locally and installed for Hermes through an explicit host-managed supervisor.

Deliverables:

- adapter/supervisor private IPC;
- persistent supervisor/service profile;
- stdio frontend over the same core; remote HTTP remains deferred;
- bounded restart/backoff/drain/upgrade fencing;
- continuity authorization from a new connection/session.

Exit gate:

- killing `aar-mcp` does not kill the logical job;
- a new authorized frontend reads the same operation and cursor;
- unrelated lanes remain available during adapter loss.

### AR-LT3 — Checkpoint-boundary recovery

**Effort class:** M/L; estimated 4–8 additional engineering weeks.
**State:** in progress; initial RLM policy/successor slice installed from
`9b7a1d9c8f8aa64fbd43a42dbfd6a6cc8b25d23a`, remaining packages open.

Deliverables:

- step-boundary RLM successor attempts;
- durable checkpoint bindings;
- programmable-workspace auto-checkpoint/yield policy;
- worker-generation restore and exclusion reporting;
- park/reconcile behavior for started-without-receipt calls.

Exit gate:

- RLM resumes only after the last certain step;
- programmable work restores the last portable checkpoint into a new generation;
- ambiguous calls are never replayed automatically;
- no exact-process-resume or exactly-once-effect claim is made.

### AR-LT4 — Distributed workflow profile

**Effort class:** L; separate project/gate.
**State:** deferred.

Cross-host placement, distributed leases, DAG orchestration, multi-tenant isolation, checkpoint migration, and compensation workflow are not prerequisites for LT0–LT3 value.

## 13. Verification matrix

The minimum scenario set is:

1. long deterministic job survives client disconnect and new-connection readback;
2. accepted intent survives runtime restart before claim and executes once;
3. worker loss before effect permits replay-safe successor;
4. workspace commit before outer receipt reconciles without duplicate mutation;
5. broker `started` without receipt parks indeterminate;
6. RLM resumes from the next certain step;
7. IPython restores a portable checkpoint into a successor generation;
8. checkpoint exclusions remain visible and unchanged;
9. same-key/same-input replay returns the prior logical operation;
10. same key with different bytes or authority scope fails closed;
11. cancel-before-claim, cancel-running, and cancel-terminal races;
12. late old-runtime/worker/lease results are rejected;
13. frontend crash does not terminate supervisor-owned work;
14. supervisor restart classifies accepted, running, indeterminate, and terminal work correctly;
15. event pagination, retention, redaction, and artifact disclosure bounds;
16. same-principal continuity succeeds only with valid authority; foreign principal/session fails;
17. supported Python and exact-wheel checks for the final candidate;
18. fresh Codex and Hermes rows only after the shared runtime candidate is frozen.

A continuity claim requires T3 process-loss evidence. MCP catalog visibility, setup success, status polling, or a mock-only test cannot substitute for it.

## 14. Expected capability coverage

These are planning estimates, not measured reliability:

- client disconnect with a live durable runtime: 90–98%;
- ephemeral frontend restart with a live supervisor: 85–95%;
- accepted/no-effect operation after supervisor restart: 90–98%;
- bounded RLM at a completed step boundary: 75–90%;
- programmable workspace at a portable checkpoint boundary: 60–80%;
- arbitrary in-flight Python/native state: 20–45%;
- generic exactly-once external effects: not claimable.

For AAR-owned, replayable, or explicitly checkpointed work, LT0–LT3 can cover roughly 80–90% of practical continuity needs. Across arbitrary live state and external effects, 60–75% is a more honest total coverage estimate.

## 15. Immediate next decision

Proceed to LT3-C/D only as a bounded portable-checkpoint slice: define checkpoint eligibility and
exclusions, restore into a new programmable-workspace generation, and reject stale handles and
environment drift. Keep general effect adapters in LT3-E and do not reinterpret the installed RLM
successor result as arbitrary IPython process resurrection.

The Prime Agent worker option remains complementary. A future Prime adapter can be represented as
another retained worker behind the same durable operation, lease, event, cancellation, and reconcile
contract instead of creating a second control plane.
