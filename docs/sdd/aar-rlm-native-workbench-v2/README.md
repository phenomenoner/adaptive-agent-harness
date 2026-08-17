# AR-RW — RLM-Native IPython Workbench SDD

**Specification status:** HARDENING — final fixed-point review not yet passed
**Product workstream status:** planned; source implementation not started
**Date:** 2026-08-17
**Target baseline:** Adaptive Agent Harness `v0.4.0a6`, commit `a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90`
**Owning repository:** canonical development worktree identified by `.aar-repository-role=canonical-development`
**Related work:** AR-1 programmable workspace, AR-2 portable RLM, AR-LT durable continuity, AR-MB model broker, public caller-delegated RLM
**Authority boundary:** AAR computes and persists bounded execution evidence; the host owns provider policy, credentials, child admission, privileged effects, accepted user work, and final delivery.

---

## 1. Executive decision

AAR shall provide one complete local/developer product journey in which a host starts one RLM job and the RLM natively owns a persistent, operation-scoped IPython workbench.

The workbench is not a separate tool that the main agent must manually coordinate after starting the RLM. Model-written Python runs inside the workbench and uses typed, grant-bound AAR broker facades for every AAR-authoritative model, subagent, evidence, artifact, and effect interaction. It may:

- inspect and transform live Python state;
- make bounded root or recursive model requests;
- request retained host-owned subagent work;
- query evidence;
- stage named artifacts;
- propose, but never self-authorize, privileged effects;
- checkpoint and resume from certain boundaries;
- produce one bounded terminal result for the host.

This restores the original product contract already present in the portable specification and architecture: `RlmJobSpec` with a workspace binding, model-written Python, and typed model/subagent/effect/artifact/evidence brokers. It does not introduce a second authority plane or import AHC/NOOA types.

The implementation shall be additive:

- keep `aar_rlm_execute` and `aar.rlm.v1` byte-compatible;
- add `aar_rlm_workbench_execute`, `aar_rlm_workbench_capabilities` and versioned v2 workbench schemas;
- reuse AR-1, AR-LT, AR-MB, broker-journal, checkpoint, and caller-delegated boundaries;
- keep the public plugin's curated no-arbitrary-code surface unchanged;
- make no managed-host, AHC, security-sandbox, or final-delivery claim from this workstream alone.

No partial work package may be advertised as the integrated product. The normative matrix is immutable; candidate-bound pass evidence lives in `aar.acceptance-results.v1`. `implementation_verified` requires every T0-T3 row and fault case, while `live_qualified` additionally requires separately authorized T4 evidence.

---

## 2. Source and evidence authority

### 2.1 Precedence

For this SDD:

1. executed native behavior and exact-candidate tests;
2. exact installed source at `a585ac52...`;
3. `contracts/contract-manifest.json`, generated schemas, `CONTRACTS.md`, `LIFECYCLE.md`, `MIGRATION.md`, and `migration-v6.sql` for successor behavior;
4. versioned schemas and fixtures;
5. canonical product documentation;
6. CodeGraph navigation and independent-agent summaries;
7. planning text.

Planning text never proves implementation. CodeGraph and delegated-agent output are navigation or challenge evidence only.

### 2.2 Baseline findings that constrain the design

The following are already source- or runtime-grounded:

- `RlmJobSpec v1` contains only `query`, `strategy`, and `max_steps`; it has no workspace binding.
- `RlmAction v1` exposes only `evidence.query` and `model.request`.
- `BaselineRlmStrategy` and `EvidenceSynthesisStrategy` emit fixed one- or two-step plans; no model-authored workbench loop exists.
- `RlmEngine._execute_action()` cannot execute workspace, subagent, artifact, or effect actions.
- IPython injects only `aar_artifact`, `aar_display`, and `aar_progress`; the documented typed broker facade is not reachable from model-written Python.
- the full Hermes profile registers `aar-mcp` with empty arguments; live capability readback reports `model_broker.configured=false`, despite profile prose promising host-owned RLM calls;
- DB-backed `FakeArtifactBroker` and `FakeSubagentBroker` create SQLite connections with default thread affinity and fail when dispatcher/workspace work reaches them from another thread;
- `aar_artifact(name, ...)` loses `name` when the backend converts the worker payload to an artifact reference;
- artifact sink calls happen before the cell terminal result is settled; a failed cell can leave a readable artifact;
- RLM heartbeats occur only at step boundaries; a blocking model call can outlive the fixed 30-second dispatcher lease;
- model-journal terminal/quarantine state can be overwritten by a late writer after an uncertain outcome;
- the modern caller-delegated RLM and the full programmable-workspace MCP are separate product surfaces and state stores.

The complete bounded defect inventory and evidence classes are in `defect-register.json`.

### 2.3 Declared limitations that are not reclassified as bugs

- Process isolation is containment, not a production security sandbox.
- Arbitrary live Python objects are not portable checkpoints.
- AAR does not own provider credentials, host child authority, external-effect execution, memory authority, user-session identity, or final delivery.
- The public plugin intentionally excludes arbitrary Python and remains curated.
- A terminal AAR result is a child-compute result, not source-turn closure.

---

## 3. Outcome and non-goals

### 3.1 Required product outcome

A fresh supported host can execute the following journey without manually alternating between unrelated RLM and workspace tools:

1. discover `rlm.workbench.v1` capability and required grants;
2. submit one `aar_rlm_workbench_execute` request;
3. receive an operation handle immediately or wait on the same durable operation;
4. fulfill typed model/subagent work tickets when host authority is required;
5. observe bounded progress and status;
6. receive one certain terminal `RlmWorkbenchResult` containing the validated output, named artifact bindings, usage, child lineage, and workspace disposition;
7. resolve every returned artifact by immutable reference;
8. reconnect or recover without duplicate model/subagent/effect work.

### 3.2 Non-goals

This SDD does not:

- make arbitrary Python replayable;
- add a NOOA dependency or adopt NOOA as AAR's protocol;
- make CodeGraph a core AAR schema;
- authorize raw network, shell, filesystem, or production effects from model-written code;
- make the public plugin expose IPython;
- replace AHC's accepted-work or delivery authority;
- perform automatic adaptation, promotion, or serving activation;
- remove v1 RLM or current public caller-delegated schemas;
- claim provider-signed route or usage evidence where the host supplies only caller observations.

---

## 4. Necessity and architecture decision

### 4.1 Decision: EMBED existing boundaries

The selected architecture embeds the RLM control loop into the existing supervised programmable workspace and typed broker boundaries.

It adds:

- an operation-scoped workbench binding;
- a durable broker-frame protocol between the IPython worker and supervisor;
- generic caller-delegated model/subagent work tickets;
- resumable live cells plus recovery-planner continuation when a paused stack is lost;
- staged, named artifact publication;
- an additive RLM workbench state machine and result schema.

It does not add a new daemon, database, authority owner, workflow engine, or provider client.

### 4.2 Rejected alternatives

#### Keep permanent host composition

Rejected as the target product experience. It makes the main agent perform mechanical RLM/workspace transfers, hides orchestration cost, and leaves the original typed-Python contract unreachable. Host composition remains a compatibility technique, not the completed workbench.

#### Bind v1 RLM to a live workspace implicitly

Rejected. An implicit namespace binding cannot be versioned, fenced, restored, or reviewed. The joined behavior needs an explicit additive contract.

#### Put provider credentials or arbitrary child launchers inside IPython

Rejected. It bypasses model/subagent brokers, breaks host authority, and makes uncertain outcomes unreconcilable.

#### Keep a live Python stack across all external waits

Rejected as the only recovery mechanism. It consumes a worker and lease, cannot survive process loss, and repeats the existing long-call failure. A worker process may remain as a disposable cache after suspension only if it is paused and write-fenced; correctness and ownership never depend on that process surviving. Durable continuation always restores/replays from the bound checkpoint under a successor attempt.

#### Invent a separate workflow DSL

Rejected. Python remains the orchestration language; typed broker facades are the narrow waist.

#### Adopt NOOA as a core dependency

Rejected. Progressive disclosure, typed methods, live objects, and CodeAct are useful design inputs. AAR retains its own portable schemas, brokers, persistence, and Python-version support.

---

## 5. Complete user journey

```text
Main agent / native host
    |
    | aar_rlm_workbench_execute(spec, start_only=true)
    v
AAR operation registry + RLM workbench coordinator
    |
    | create operation-scoped IPython generation
    | bind grants, budgets, route, artifact policy
    | checkpoint pre-cell state
    v
Root model planner -> typed Python cell
    |
    v
Supervised IPython worker with `aar` facade
    |
    +-- local deterministic Python / live objects
    +-- aar.model.request(...) ------> durable model work ticket
    +-- aar.agents.submit/run(...) --> durable subagent work ticket
    +-- aar.evidence.query(...)
    +-- aar.artifacts.stage(...)
    +-- aar.effects.propose(...)
    +-- aar.complete(...)
    |
    | unresolved external broker call => suspend cell
    v
Operation waiting_external, no dispatcher lease held
    |
    | host claim -> physical model/child work -> commit receipt
    v
Successor attempt restores pre-cell checkpoint
    |
    | deterministic replay returns committed broker receipts
    | execution continues until next suspension or completion
    v
Cell commit manifest
    |
    | workspace revision + staged artifacts + broker trace
    v
RLM result validation and finalization
    |
    v
Bounded result + named artifact refs + usage + lineage
    |
    v
Host decides final delivery
```

The main agent handles authority callbacks, not workspace micromanagement. One logical AAR operation owns the workbench and all successor attempts.

---

## 6. Process and authority boundaries

### 6.1 Main/managed host owns

- accepted user intent and exact user/session/channel identity;
- provider/model policy and credentials;
- physical model execution when caller-delegated;
- child-agent admission, backend selection, and physical launch;
- privileged effect authorization/execution;
- authoritative host receipts;
- cancellation policy above AAR;
- memory authority, outbox, and final delivery.

### 6.2 AAR owns

- the logical workbench operation and successor attempts;
- operation-scoped workspace identity, generation, and revision;
- Python cell source, checkpoint, replay, and broker-call sequence;
- typed work-ticket state and receipt reconciliation;
- AAR budgets and deadlines;
- artifact staging/publication and immutable references;
- RLM result, usage projection, events, and recovery decisions.

### 6.3 Worker owns only live execution

The worker owns one live IPython process generation and request-local protocol state. It owns no credentials, durable ticket truth, final artifact publication, operation terminal state, or host delivery.

### 6.4 AHC compatibility

An AHC adapter may bind one AHC accepted task to one AAR workbench operation. AAR operation/attempt/workspace IDs remain subordinate execution evidence. The adapter translates pending model/subagent/effect intents into AHC-owned work and commits opaque authoritative receipts. No AHC type enters AAR's portable schemas.

---

## 7. Additive contracts

Exact fields, defaults, ceilings, cross-field conditions, authorization context and result unions are frozen by `contracts/aar-rlm-workbench-v1.schema.json`, `CONTRACTS.md`, and `fixtures/valid-workbench-execute.json`. The structures below are explanatory only when they do not contradict those artifacts.

### 7.1 `aar.rlm-workbench-job.v1`

The canonical valid request is `fixtures/valid-workbench-execute.json`. Its route catalog is `fixtures/valid-route-catalog.json`. The adversarial fixtures prove that caller-delegated blocking calls, job-supplied grants, incomplete route bindings, and remote schema references fail at the intended validation layer.

Normative rules:

- v1 workbench jobs use operation-scoped workspace ownership only. A borrowed live workspace is excluded until an exclusive ownership-transfer contract exists.
- `route_binding` is immutable for one logical job unless the host starts a different job.
- caller-delegated and service-managed modes never silently fall back into one another.
- `caller_delegated` admission requires `start_only=true`; a blocking MCP call cannot both return a ticket and depend on that same caller to service it. A host-native duplex driver may hide polling, but it obeys the same ticket state machine.
- `service_managed` may use `start_only=false` only when an injected configured broker and independent lease keeper are present.
- every budget is cumulative across attempts, successor continuations, correction/recovery planner calls, and reconnects.
- grant IDs and host maxima live only in `McpRlmWorkbenchMutationContext`; the job schema rejects a `grants` property.
- `output_contract.schema` is bounded, local-reference-only, and digest-bound at admission; remote schema resolution is forbidden.
- `metadata` is a bounded scalar map with canonical keys and bytes; it is not an extension escape hatch.

### 7.2 `aar.rlm-directive.v1`

The root planner returns one strict discriminated directive:

```text
execute_cell(code, expected_result_hint)
finalize(output, artifact_stage_ids)
abstain(reason)
```

Markdown code-fence scraping is not a contract. The runtime, not the caller, owns the single generated `RlmDirective` schema used by initial, correction, recovery and finalizer planner requests. `aar_rlm_workbench_capabilities` publishes its fixed `aar.rlm-directive.v1` version and digest; job input cannot replace or weaken it. The model broker request embeds those exact schema bytes, digest and maximum response bytes. The caller/model broker may return text, but AAR validates the exact bytes before creating a directive; invalid output enters a bounded correction loop or fails certain. `execute_cell` source is persisted by digest before execution.

### 7.3 `aar.workspace-broker-frame.v1`

The worker/supervisor IPC becomes a bounded frame stream rather than one request/one terminal line.

Worker-to-supervisor frame kinds:

```text
stdout
stderr
rich_display
progress
broker_intent
artifact_stage_intent
completion_intent
execute_result
rebind_ack
worker_protocol_error
```

Supervisor-to-worker frame kinds:

```text
execute_request
broker_receipt
broker_suspend
rebind_prepare
rebind_commit
rebind_abort
cancel
supervisor_protocol_error
```

Every frame carries:

- protocol version;
- operation ID;
- attempt fence;
- workspace ID/generation/revision;
- cell execution ID;
- monotonically increasing frame sequence;
- explicit `worker_to_supervisor | supervisor_to_worker` direction;
- payload digest and byte length;
- total deadline;
- broker call ordinal, required only for broker-intent/receipt/suspend frames and null otherwise;
- a fixed `committed` value: true only for supervisor frames projected from durable authority, false for worker proposals and protocol errors.

`WorkspaceBrokerFrame` is a closed discriminated union. Every kind has a strict payload with required fields and `additionalProperties: false`; empty, missing-field, extra-field, wrong-direction and wrong-authority variants fail schema validation. `artifact_stage_intent` and `completion_intent` are proposals only: the worker cannot call a broker, write authoritative operation state, stage/promote artifacts, cancel work or grant rebind authority. The supervisor validates current attempt authority and performs those mutations through TX-C/TX-G1–G3/TX-H/TX-I.

Unexpected sequence, identity, direction, authority generation, size or digest fails closed. Captured user stdout/stderr can never be parsed as protocol frames.

### 7.4 `aar.caller-work-ticket.v1`

One generic durable ticket supports model, retained subagent, evidence, and effect-proposal intents while preserving method-specific contracts. Exact ticket/request/observation/tool schemas are in `contracts/aar-caller-work-v1.schema.json`; the state automaton and send-start linearization point are normative in `LIFECYCLE.md` §4.

Key rules:

- `aar_broker_work_claim` creates a pre-send `send_reserved` claim bound to principal/session, adapter generation, claim fence, physical attempt identity and idempotency identity;
- `aar_broker_work_cancel_before_send` durably settles `pending | send_reserved` as certain-no-send while all send evidence remains null;
- no provider/child dispatch is legal before `aar_broker_work_mark_send_started` atomically validates operation control/cancellation revision, suspension/ticket revision, deadline, claim expiry/fence and commits `send_started`;
- expired `send_reserved` claims are safe to reclaim; `send_started` is conservatively may-have-sent and requires lookup/idempotency/reconciliation;
- candidate receipts are immutable; only the current reconciler may settle authoritative state;
- caller wait is a workbench phase with no active dispatcher lease;
- route/usage receipts, host observations and provider attestation remain distinct.

### 7.5 `aar.broker-context.v2`

Every broker mutation adds execution fencing absent from v1:

```text
parent operation
current attempt ID
runtime generation
supervisor/dispatcher generation
lease epoch when an attempt is active
workspace ID/generation/revision
cell execution ID
broker call ordinal
principal/session/lane
required grant
idempotency key
cumulative deadline and budget reservation
```

A stale attempt may contribute an immutable candidate receipt for its exact physical external attempt, but it may not create or mutate a broker call, RLM step, artifact publication, successor admission, finalization, or terminal projection.

### 7.6 `aar.artifact-binding.v1`

`ArtifactReference v1` remains the immutable content identity. `contracts/aar-artifact-publication-v1.schema.json` adds the closed `ArtifactBinding`, `ArtifactStage`, `CellCommitManifest`, and `FinalizationManifest` contracts. A binding preserves normalized logical name, media type, content digest/size, role, operation, workspace generation, and cell identity.

Logical names are normalized relative POSIX-style names, unique under the result manifest, and never host paths. Duplicate names fail the cell commit. Exact invalid components and visibility/atomicity rules are in `CONTRACTS.md` §12 and `LIFECYCLE.md` §9.

### 7.7 `aar.rlm-workbench-result.v1`

The terminal result contains:

- certainty and terminal status;
- output validated against the admitted output schema;
- named artifact bindings;
- cumulative model/subagent/cell/recovery/usage totals;
- bounded step and broker trace references;
- child handles/results and unresolved-child count;
- final workspace/checkpoint binding and disposition;
- abstention/failure evidence where applicable;
- no credential, raw provider token, host path, or final-delivery claim.

A result with unresolved required tickets, uncommitted artifacts, output-schema failure, or unknown privileged effect cannot be `succeeded`.

### 7.8 Broker request v2 deltas

The v1 broker contracts remain byte-stable. Workbench calls use additive, strict request schemas:

- `aar.broker-contract.model-request.v2`: prompt, admitted response-contract schema/digest/max bytes, and the exact job route binding;
- `aar.broker-contract.subagent-submit.v2`: bounded task, host-authorized child profile, result-contract digest/max bytes, artifact policy, parent recursion depth, and child budget reservation;
- `aar.broker-contract.subagent-result.v2`: retained child execution ID, result-contract digest, deadline, and result ceiling; `aar.agents.await_result()` is the facade convenience and polling loops are not the broker contract;
- `aar.artifact-stage.v1`: normalized logical name, role, media type, exact size/digest, bytes, and redaction marker;
- `aar.broker-contract.evidence-query.v2`: bounded query plus admitted result ceiling and evidence-source binding.

The synchronous-looking Python facade is normative. `aar.model.request(...)`, `aar.agents.submit/run(...)`, and `aar.agents.await_result(...)` either return an already committed certain receipt in the current continuation or trigger an internal controlled suspension; they never expose an unpersistable future/coroutine as durable state. Cell call identity derives from cell execution ID, method, ordinal and canonical request digest; planner/recovery/finalizer calls use the non-cell logical-owner variant. A lost Python stack is not replayed automatically; `LIFECYCLE.md` §6 defines live resume or recovery-planner continuation.

---

## 8. RLM workbench state machine

### 8.1 Logical operation phases

```text
accepted
  -> preparing_workspace
  -> running
  -> waiting_external
  -> running               (successor attempt after certain commit)
  -> checkpointing
  -> running
  -> finalizing
  -> succeeded | failed | cancelled | timed_out | indeterminate | parked
```

`RlmWorkbenchPhase` is a separate closed state contract; it does not extend frozen `OperationState v1`. `waiting_external` has no active attempt lease and contains exact ticket/deadline bindings. Generic operation APIs project workbench phases according to `CONTRACTS.md` §9 (`waiting_external` projects to `accepted`; `parked` projects to `indeterminate`). No worker owns mutation authority while waiting. A paused worker may survive only as a write-fenced disposable cache and must be replaceable by recovery-planner continuation.

### 8.2 Attempt outcomes

An attempt may end as:

```text
succeeded_terminal
failed_certain
suspended_external
cancelled_certain
indeterminate
lease_lost
worker_lost
```

`suspended_external` is not an operation failure. It records the certain pre-cell checkpoint, cell source digest, call sequence prefix, and pending ticket before releasing ownership.

### 8.3 Transition rules

- only the current fenced attempt may mutate running work;
- entering `waiting_external` atomically publishes pending ticket identity and ends the attempt;
- committing a ticket moves the operation back to dispatchable state and creates a successor attempt;
- cancellation while waiting invalidates unclaimed tickets and fences late commits;
- a claimed ticket with unknown physical outcome becomes `outcome_unknown` and parks until reconciliation;
- terminal/quarantined states are monotonic; late writers receive the existing authoritative state;
- timeout is cumulative across attempts and waiting periods;
- terminal operation state never proves host final delivery.

---

## 9. Durable Python broker calls

### 9.1 Native facade

The worker injects one documented object:

```python
aar.model.request(...)
aar.agents.submit(...)
aar.agents.await_result(...)
aar.agents.run(...)
aar.evidence.query(...)
aar.artifacts.stage(...)
aar.effects.propose(...)
aar.progress(...)
aar.display(...)
aar.complete(...)
```

These methods have synchronous Python semantics. External work is implemented by a controlled suspension. If the exact paused worker survives, a fenced successor can deliver the receipt and resume the live stack. If that stack is lost, AAR restores the pre-cell checkpoint and invokes a bounded recovery planner; it never automatically re-executes arbitrary Python. `aar.agents.run(...)` is bounded `submit + await_result`; it does not bypass retained-child identity.

The facade is generated from the capability-bound, version-qualified broker registry. Missing grants remove or deny methods; documentation visibility never grants authority. Progress/display frames emitted by an uncommitted cell are provisional and carry stable event IDs; only cell-commit promotion makes them committed history.

### 9.2 Suspend, resume, or recover

Before a broker-interactive cell:

1. validate and persist a portable pre-cell checkpoint or explicitly record the unsupported live-state boundary;
2. persist cell source/digest, admitted budgets, workspace binding, and a new cell execution ID;
3. execute under the current attempt/control/workspace fences.

When the facade needs host work:

1. form request identity from the logical owner, method contract ID, ordinal, and request digest;
2. emit one validated broker intent;
3. atomically persist the suspension, pending caller ticket, workbench phase, attempt release, worker write fence, and waiting event as `LIFECYCLE.md` TX-C;
4. settle the caller ticket through the pre-send/send-start/candidate-receipt automaton;
5. admit exactly one successor through the durable outbox.

The successor resumes the exact paused stack only when the same worker generation remains alive and write-fenced ownership is reacquired. Otherwise it restores the pre-cell checkpoint, marks the interrupted cell `lost_before_commit`, and requests a recovery directive with source digest, committed history, certain external receipts, artifact/checkpoint bindings, and loss reason. The recovery directive creates a new cell ID. The lost cell is never replayed automatically.

### 9.3 Recovery eligibility

A broker-interactive cell must start from checkpoint-portable or artifact-backed state. In `trusted_local`, raw filesystem/network/subprocess/time/random/native-extension effects are outside AAR authority. Their possible occurrence forbids automatic replay; AAR does not claim to detect or compensate them. If required state is unportable or recovery planning cannot produce a valid continuation, the workbench parks and projects legacy `indeterminate`.

A future managed-restricted deterministic-replay profile requires a separately negotiated and verified contract; it is not part of AR-RW v1.

### 9.4 Root planner loop

The root planner receives only bounded objective/context, workspace synopsis, prior cell result/event summary, artifact bindings, and budget remainder. It emits an `execute_cell`, `finalize`, or `abstain` directive. Planner correction loops are bounded. Recursive model calls from the cell use the same job route policy and cumulative budget unless an explicitly admitted child route policy says otherwise.

---

## 10. Subagent semantics

- AAR never launches an ungoverned process from model-written Python.
- `aar.agents.submit()` produces a typed host-owned child-work ticket or calls an injected native `SubagentBroker`.
- The host returns a retained child handle before AAR treats submission as accepted.
- child status/result is read through exact handle, parent operation, host generation, and receipt digest;
- recursive depth, child count, total wall time, and result bytes are cumulative job budgets;
- child artifacts are imported by immutable reference and rebound under parent result provenance;
- unknown child submission outcome is reconciled, never blindly resubmitted;
- cancellation requests propagate through the broker, but AAR reports host refusal/unknown honestly;
- a child terminal result does not close the parent or deliver to the user.

A local reference fake remains available for conformance tests. The Hermes/AHC product profile must inject a real caller-delegated or native subagent adapter before advertising recursive-agent capability.

---

## 11. Artifact staging and publication

### 11.1 Required invariant

No artifact is part of the user-visible workbench result merely because bytes reached SQLite.

A cell produces staged artifact candidates. Publication requires a certain cell commit manifest binding:

- normalized logical name;
- media type;
- content digest and size;
- operation/attempt/workspace/cell provenance;
- admitted artifact policy and budget;
- terminal cell status or explicit diagnostic role.

### 11.2 Commit sequence

1. worker returns staged bytes or references under bounds;
2. content-addressed storage writes idempotently as `staged`;
3. coordinator persists a `CellCommitManifest` containing result/event digests and artifact bindings;
4. current workbench may resolve manifest-authorized intermediate stages, but they are not final product artifacts;
5. reconciler completes an interrupted cell promotion if and only if the manifest is certain;
6. the single `FinalizationManifest` CAS authorizes terminal named artifact bindings and the final result together.

Failed, cancelled, suspended, or protocol-invalid cells do not publish deliverable artifacts. Optional diagnostic artifacts require an explicit `diagnostic` role and separate API projection; they are never silently mixed into final deliverables.

### 11.3 Broker repair

Replace DB-backed production use of the `FakeArtifactBroker`/`FakeSubagentBroker` connection pattern with thread-safe repositories. A single `check_same_thread=False` change is insufficient without serialization and close ownership.

Permitted implementation patterns:

- connection-per-call repositories under SQLite WAL; or
- a shared connection with `check_same_thread=False`, a dedicated re-entrant lock, and closed-state fencing.

Whichever pattern is selected must pass concurrent put/read/close and dispatcher-worker tests. Keep lightweight in-memory fakes for unit/conformance tests.

---

## 12. Lease, fencing, cancellation, and late writers

### 12.1 Independent lease keeper

Every active service-managed attempt gets an independent heartbeat keeper, using the proven programmable-workspace pattern. A blocking model, subagent, artifact, or worker call cannot be the component responsible for renewing its own lease.

Caller-delegated waiting does not keep an attempt or lease active.

### 12.2 Fence propagation

Every inner durable write carries or validates the current attempt fence:

- RLM step append;
- broker journal start/commit/failure;
- model execution journal settlement;
- subagent handle/result;
- artifact stage/commit;
- workspace binding/revision;
- terminal result projection.

A stale attempt may only read immutable certain receipts for replay.

### 12.3 Monotonic terminal state

Model/subagent/ticket/journal settlement uses compare-and-set transitions. `quarantined`, `committed`, `failed_certain`, `cancelled`, and `outcome_unknown` cannot be overwritten by a late in-flight writer. Reconciliation and worker completion race tests are mandatory.

### 12.4 Cancellation

- operation cancellation is durable intent;
- active worker execution receives interrupt, then bounded termination;
- unclaimed tickets become cancelled;
- claimed external work attempts host cancellation and remains unknown until receipt/reconciliation;
- staged artifacts are not promoted after cancellation;
- cancellation never guesses the result of a physical model, child, or effect call.

---

## 13. Budgets and observability

The following are first-class cumulative counters:

- planner model calls;
- recursive model calls;
- model input/output/total tokens by evidence tier;
- subagent submissions and depth;
- Python cells, suspensions, and replays;
- wall time running vs waiting;
- workspace event/output bytes;
- artifact count and bytes;
- result bytes;
- unknown/quarantined calls;
- main-agent claim/commit interactions.

Every broker facade call emits a bounded trace span containing method, request digest, receipt digest, evidence tier, timing, budget delta, attempt/cell identity, and certainty. Trace data never includes credentials or unbounded prompt/output content.

Route reporting distinguishes requested, caller-observed, host-receipt-bound, and provider-attested evidence. No-silent-fallback is enforced per job.

---

## 14. Security posture

Model-written Python is untrusted at the data/protocol boundary, but the current local developer profile executes it with the host account's ordinary Python permissions and is not an OS security sandbox.

- The facade grants no AAR-managed capability beyond host-issued broker grants.
- `effects.propose()` creates an AAR intent; it never executes the effect through AAR.
- Provider credentials remain outside the worker.
- Child launch authority remains outside the worker.
- Protocol frames and artifacts have strict byte/count ceilings.
- Content and trace storage redact secrets and never persist environment dumps.
- Public-plugin policy remains no arbitrary Python.
- In `trusted_local` mode, raw Python filesystem/network/subprocess behavior remains physically possible under ambient OS authority, sits outside AAR receipts/replay guarantees, and must be reported as such.
- A job requiring a managed security guarantee must request an admitted `managed_restricted` workspace profile; absence is a required-on-use admission failure, never a silent downgrade.
- This SDD does not claim that `managed_restricted` already exists; its availability requires separate T3 security-profile evidence.

---

## 15. API, compatibility, and migration

### 15.1 Additive API

Add to the full developer MCP/direct SDK:

```text
aar_rlm_workbench_execute
aar_rlm_workbench_capabilities
aar_rlm_workbench_status
aar_broker_work_claim
aar_broker_work_mark_send_started
aar_broker_work_commit
aar_broker_work_reconcile
```

Existing operation status/events/cancel/reconcile and artifact resolution tools remain as legacy projections or shared controls. `aar_rlm_workbench_status` is the typed phase/ticket/workspace/result read model.

The full profile also adds per-method backend availability for model, subagent, evidence, artifact, and effect methods: `configured`, `backend_kind`, `evidence_tier`, and `reference_only`. Reference fakes may be advertised only when `reference_only=true`; they never satisfy production/full-journey admission. A profile claiming caller-delegated model/subagent work must include and verify a real host driver.

`aar_rlm_execute` v1 remains unchanged. The fixed Hermes MCP Sampling flag remains deprecated compatibility-only. The new workbench path uses caller-delegated tickets or an injected production broker registry.

### 15.2 Schema/version changes

Candidate additions:

- `aar.rlm-workbench-job.v1`;
- `aar.rlm-directive.v1`;
- `aar.broker-contract.model-request.v2` and response-contract binding;
- `aar.broker-contract.subagent-submit.v2` and `aar.broker-contract.subagent-result.v2`;
- `aar.artifact-stage.v1` and `aar.broker-contract.evidence-query.v2`;
- `aar.workspace-broker-frame.v1`;
- `aar.caller-work-ticket.v1`;
- `aar.broker-context.v2`;
- `aar.artifact-binding.v1`;
- `aar.rlm-workbench-result.v1`;
- additive workbench-phase, suspension, caller-attempt, outbox, manifest and SQLite migration contracts while frozen `OperationState v1` remains unchanged;
- additive MCP surface version and per-method backend-availability rows.

Exact generated digests are in `contracts/contract-manifest.json`; implementation candidates must either match or revise and re-review the SDD package.

### 15.3 Data migration

- Existing v1 jobs, steps, artifacts, workspaces, model calls, and operations remain readable.
- New tables/columns are additive and guarded by migration registry.
- No destructive down-migration is required.
- Before upgrade, fence admissions/writers/dispatch and create a verified SQLite backup API snapshot; a raw copy of a WAL-mode database is forbidden.
- Commit the schema registry row and `migration_v6_attestations` row atomically, and maintain the separately fsynced hash-chained cutover authority defined by `aar-migration-cutover-v1.schema.json`.
- The prior executable and snapshot may reopen only through `candidate_active -> rolled_back_before_write`, before any candidate admission, mutation, or effect authorization.
- After `post_snapshot_write`, candidate failure stops admission/dispatch, retains evidence, and recovers forward on v6; the prior snapshot is permanently stale.
- v2 rows are not rewritten as v1 success.

### 15.4 Public plugin

The public plugin's current caller-delegated RLM remains a separate curated product. Shared ticket semantics may be extracted into a transport-neutral core model only if public v1 JSON bytes and lifecycle tests remain compatible. The public plugin does not gain arbitrary Python from this SDD.

---

## 16. Failure semantics

| Situation | Required result |
|---|---|
| invalid spec/grant/route/budget | fail before admission |
| workspace create fails before execution | failed_certain |
| worker lost before broker intent | restore pre-cell checkpoint and recovery-plan a new cell, or park |
| broker request identity/digest conflicts | quarantined/parked; no successor dispatch |
| caller ticket remains `send_reserved` until claim/cumulative deadline | CAS to `cancelled_before_send` with local settlement receipt if send-start never committed; otherwise reconcile may-have-sent state |
| `send_started` physical model/child outcome unknown | outcome_unknown; lookup/cancel/reconcile, no blind retry |
| late callback after cancellation/terminal | append matching candidate receipt; current reconciler decides, settled truth is monotonic |
| artifact staging fails | cell fails; no deliverable publication |
| promotion interrupted after cell manifest | reconcile idempotently |
| output schema invalid | correction loop within budget, then failed_certain |
| unportable live state needed for recovery | explicit unsupported/parked result |
| main agent disconnects | operation/tickets remain durable; no implicit cancel |
| AAR terminal result exists but host delivery fails | AAR remains terminal; host owns delivery recovery |

---

## 17. Test and acceptance strategy

`acceptance-matrix.json` is immutable and normative; `fault-matrix.json` supplies required atomic interleavings. Candidate/result evidence validates against `contracts/aar-acceptance-evidence-v1.schema.json` and is not written back into the matrix. `validate_sdd.py` without `--results` reports only `STRUCTURALLY_VALID`. Result adjudication uses `python3 validate_sdd.py --results <bundle>/results.json --review-trust <authorized-trust-store>.json`; `jsonschema`, `referencing`, and `cryptography` are validator dependencies.

The matrix includes:

- T0 contract/fixture/migration tests;
- T1 pure state, budget, CAS, thread, artifact, resume/recovery, and compatibility-projection tests;
- T2 direct host and real IPython subprocess integration;
- T2 full MCP caller ticket lifecycle with fake model/subagent hosts;
- T3 installed-wheel, attached-supervisor, process-loss, stale-writer, restart, and fresh-host journeys;
- one separately authorized T4 Luna/max qualification with tools/route/usage evidence labels;
- compatibility tests proving v1 and public-plugin behavior do not drift.

Required acceptance is batch-complete. Passing unit tests or one happy-path cell does not establish product readiness. `implementation_verified` requires every required T0-T3 row and linked fault case; `live_qualified` additionally requires authorized T4 evidence and an authorization receipt.

---

## 18. Implementation impact map

Primary source ownership:

```text
src/aar/rlm_workbench_models.py          new v2 models
src/aar/runtime/rlm_workbench.py         new coordinator/strategy
src/aar/runtime/caller_work.py           durable generic tickets
src/aar/runtime/artifact_publication.py  staging/commit/reconcile
src/aar/runtime/ipython_worker.py         typed facade + framed IPC
src/aar/runtime/ipython_backend.py        frame router + suspension/result handling
src/aar/runtime/brokers.py                thread-safe repos + v2 context/fences
src/aar/runtime/model_broker.py           monotonic settlement/fence integration
src/aar/runtime/registry.py               suspension/outbox/fence transitions; frozen OperationState projection
src/aar/runtime/dispatcher.py             lease keeper/suspension handling
src/aar/runtime/reference_host.py         construction/lifecycle injection
src/aar/mcp/server.py                     additive tools and capabilities
src/aar/mcp/models.py                     tool result/input projections
src/aar/schema_generator.py               schemas/fixtures/digests
skills/aar-operations/SKILL.md             complete guided workbench workflow
profiles/**                                generated version/digest projections
```

Required test ownership:

```text
tests/test_rlm_workbench.py
tests/test_caller_work.py
tests/test_ipython_workspace.py
tests/test_broker_facade.py
tests/test_model_broker.py
tests/test_durable_dispatcher.py
tests/test_long_task_continuity.py
tests/test_mcp_server.py
tests/test_contracts.py
tests/test_profiles.py
integration installed-wheel/fresh-host probes
```

Implementation agents must run current CodeGraph impact queries from a current implementation worktree before edits. The current product-design worktree is behind the installed release commit and has a user-owned dirty WAL; no agent may reset or overwrite it.

---

## 19. One coherent implementation candidate

The build may be internally ordered, but no internal package is a product milestone:

```text
contracts + RED fixtures
        |
shared broker/thread/fence/artifact hardening
        |
worker framed IPC + typed facade
        |
caller work tickets + waiting/successor lifecycle
        |
RLM workbench coordinator + result
        |
MCP/profile/skill/migration integration
        |
full atomic acceptance batch + independent review
```

Parallel work is allowed only where ownership is disjoint and contracts are frozen. The candidate remains `planned` or `implemented-unverified` until the full journey passes.

---

## 20. Rollback and cutover

1. implement in a clean canonical worktree based on the exact selected successor of `a585ac52...`;
2. preserve the existing dirty canonical WAL and unrelated user work;
3. build exact wheel and generated assets once executable bytes stabilize;
4. test migration on verified SQLite-backup snapshots, including committed WAL-only pages and populated v5 rows, never on a raw live-file copy;
5. stop and preserve evidence on any unknown effect, migration mismatch, fixture drift, stale-writer acceptance, or partial artifact publication;
6. cut over only after independent review of the immutable candidate and durable `prepared`/`candidate_active` authority records;
7. append/fsync `post_snapshot_write` before the first candidate admission, mutation, or external dispatch, then restart the owning host once and run the required native journey;
8. prior-snapshot rollback is allowed only through `rolled_back_before_write`; after `post_snapshot_write`, recover forward on v6 and never select the stale snapshot.

---

## 21. Completion claim

AR-RW may be called **verified** only when the same immutable candidate proves all of the following:

- one main-agent request starts one native RLM workbench operation;
- model-written Python uses persistent IPython and typed model/subagent/evidence/artifact/effect-intent facades;
- caller-delegated model and child work survives disconnect/restart without duplicate execution;
- DB-backed brokers are thread-safe and close-safe;
- artifact names, provenance, staging, atomic publication, failure, and recovery are correct;
- lease keeping and stale-attempt fencing hold across long calls and races;
- terminal/quarantine states are monotonic;
- cumulative budgets and cancellation are enforced;
- final result and artifacts are bounded and resolvable;
- v1 RLM, current public plugin, and host authority boundaries remain compatible;
- installed-wheel and fresh-host evidence pass;
- independent review finds no actionable required-row gap.

Anything less is evidence of progress, not the completed product.
