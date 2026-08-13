# AR-LT3 — Checkpoint- and Effect-Aware Continuation Implementation Plan

**Status:** verified and installed; schema v5, runtime/dispatcher generation 13
**Updated:** 2026-08-12
**Owning workstream:** [LONG-TASK-CONTINUITY-PLAN.md](LONG-TASK-CONTINUITY-PLAN.md)
**Cross-project coordination:** maintained in the separate AHC-by-AAR planning workspace; not normative to AAR core

## 1. Objective

Resume eligible long work from the last certain, portable boundary by creating a successor attempt bound to an RLM step receipt or workspace checkpoint. External broker/effect uncertainty is reconciled before continuation. Unsupported live state remains explicit loss or needs-user.

AR-LT3 does not promise arbitrary instruction-level, stack-frame, native-process, socket, generator, or token-stream continuation. It does not provide universal exactly-once external effects.

### Entry checkpoint — 2026-08-11

AR-LT3 starts after the installed AR-LT2 source candidate
`38f338255f38b266f0cbe4d87db6f659274d80c6` proved durable supervisor/frontend separation,
process/worker fencing, exact-wheel platform compatibility, and fresh installed-host readback.

The first authorized implementation slice is deliberately narrower than the full phase:

1. freeze a versioned operation-kind recovery-policy registry and explicit eligibility decisions;
2. bind the latest certain RLM step, cumulative budget/deadline/cancel state, input digest, runtime and
   dispatcher generations, attempt/lease fence, and broker receipts into a successor decision;
3. create a successor attempt only after the predecessor is durably classified and all original
   broker calls are either receipt-backed or explicitly indeterminate;
4. prove fail-closed behavior for input/environment mismatch, unresolved broker calls, stale
   generations, exhausted budgets, expired deadlines, and cancellation intent.

Automatic programmable-workspace checkpoint restore, effect reconciliation adapters, native worker
reattachment, and managed-host integration remain later work packages. This entry checkpoint changes
no public effect authority, AHC gate, activation, or delivery claim.

### Bounded slice closeout — 2026-08-11

Source commit `9b7a1d9c8f8aa64fbd43a42dbfd6a6cc8b25d23a` and package
`adaptive-agent-runtime==0.3.0a0` verify the initial RLM portions of LT3-A, LT3-B, LT3-F, and LT3-G:

- `rlm.step-boundary.v1` is frozen and digest-bound to the operation before durable dispatch;
- SQLite schema v4 stores recovery-policy bindings, RLM continuation boundaries, and policy/boundary
  digests on recovery decisions;
- the planner binds exact operation, predecessor attempt, runtime/dispatcher generations, lease epoch,
  input and environment digests, committed steps, broker traces, cumulative usage, deadline, and
  cancellation state;
- a receipt-backed call is reused without demanding unused model budget, while unresolved, malformed,
  reordered, foreign-grant, or digest-mismatched broker evidence parks or quarantines;
- successor admission and its recovery decision/boundary are one registry transaction, and a stale
  lease/generation cannot leave a partial decision or accepted successor;
- invalid persisted policy evidence is isolated to its operation and does not prevent healthy durable
  work from running.

Executed evidence:

- full repository suite: `199 passed, 1 skipped` in `335.32s`; the skip is the platform-guarded
  Windows `GetProcessTimes` path, which passed separately in the native Windows wheel probe;
- contract, MCP asset, host-profile, Ruff, and new-file format verification passed;
- exact wheel SHA-256
  `cce45b4f8ac3af83beb43812aad9f8798ad8abd9d43dcf4f8dab399305a507f8`,
  300,806 bytes and 113 ZIP members;
- a fresh Linux Python 3.11 wheel install passed all ten compatibility-smoke checks through an exact
  wheel supervisor/frontend pair; a fresh native Windows Python 3.13 install passed process identity,
  credential rejection, TCP supervisor attachment, 30-tool discovery, and capabilities readback;
- an online backup of the installed LT2 schema-v3 database migrated to v4 under the exact LT3 wheel;
  LT2 rejected that copy as a newer schema, while a pre-cutover read confirmed the authoritative
  database remained v3 and passed `quick_check`.

This was not yet the full AR-LT3 exit. LT3-C/D workspace checkpoint selection and new-generation
restore, LT3-E general broker/effect reconciliation adapters, the remaining fault matrix, and
managed-host mapping remained open before cutover.

### Installed cutover closeout — 2026-08-11

The exact `0.3.0a0` wheel identified above is now installed behind the operator-selected Windows
Scheduled Task and durable WSL supervisor. The package did not create or alter that service policy.

Executed installed evidence:

- before replacement, the serving LT2 process identity, executable, runtime home, task action,
  generation 11, schema v3, database integrity, terminal-only operation set, and completed dispatch
  rows were read back;
- a complete LT2 uv-tool environment, exact LT2 wheel, scheduled-task XML, and an untouched v3
  database were retained together; the LT2 reader opened a separate copy and the canonical rollback
  database digest stayed unchanged;
- the scheduled supervisor restarted from the installed LT3 executable, published fresh exact process
  identity, and advanced both runtime and dispatcher to generation 12;
- the authoritative database migrated additively to schema versions 1 through 4 and returned
  `quick_check=ok`;
- a fresh `hermes mcp test aar` process discovered all 30 tools, and the current native MCP session
  read package `0.3.0a0`, attached-supervisor mode, generation 12, the v7 surface, and the `0.8.0`
  operation skill;
- a bounded live durable RLM canary was accepted, dispatched, and completed with one authoritative
  model receipt; its admission-time recovery policy binding was present in the v4 registry;
- a second supervisor candidate failed closed because the exact live process remained named in
  discovery; generation stayed 12 and one exact supervisor process remained;
- a two-second idle sample was approximately 0.5% CPU, supervisor private material remained mode
  `0600` below a mode-`0700` private directory, the task remained running, and no active or
  indeterminate operation remained;
- exactly three usable rollback generations remain retained, including the untouched pre-LT3 v3
  rollback.

This installed closeout verifies only the first bounded policy-bound RLM successor slice. It does not
complete LT3-C/D workspace restore, LT3-E general broker/effect reconciliation, generic exactly-once
effects, arbitrary running-cell resurrection, activation, final delivery, or AHC managed-host
admission.

### Source implementation closeout candidate — 2026-08-11

The current source candidate completes the bounded LT3-C/D/E implementation while the installed
supervisor remains on the earlier schema-v4 wheel described above. The candidate is not a live
cutover claim until fresh repository, package, rollback, supervisor, and MCP readback gates pass.

- additive schema v5 persists an exact predecessor-bound workspace checkpoint selection before
  restore and records the verified new-generation boundary before atomic successor admission;
- plain-Python and IPython recovery reject stale session, generation, revision, backend,
  environment, manifest, artifact, exclusion, or selection evidence; they converge across a
  restore-before-admission process loss, and real IPython worker loss recovers on both the same live
  host and a restarted host without reusing a lost predecessor receipt or creating multiple
  successor generations;
- the broker journal retains canonical request bytes and reconciliation evidence; authoritative
  artifact and subagent receipts are recovered without replay, while any provider-backed safe-read
  recovery first requires the persisted envelope capability digest to match the current host
  profile; unknown model/effect outcomes remain pending or quarantined without replaying a
  write-class `effect.propose` call;
- compensation requires explicit caller intent, original-envelope provenance, and a separate
  unexpired current `effect.propose` grant in addition to current `operation.reconcile` authority;
  its journal admission first obtains a SQLite write reservation, then rechecks the effective
  authority deadline, durable cancellation flag, and exact control revision before inserting the
  intent. The deadline is checked again immediately after intent commit and before provider
  invocation. Expiry while waiting for the reservation creates no intent and invokes no provider;
  expiry after intent commit records an explicit failed intent and still invokes no provider. It is
  forbidden after cancellation or deadline expiry, and no reconciliation path exposes or executes
  `effect_execute`;
- durable startup orders effect reconciliation before RLM and workspace successor planning, so a
  continuation decision never precedes authoritative receipt classification; capability-profile
  drift parks the affected RLM as `needs_user` with
  `recovery_capability_digest_mismatch`, performs zero provider calls, and does not abort dispatcher
  startup for unrelated operations.

Source verification includes real IPython worker loss, restore/admission crash convergence, exact
workspace policy drift and partial-checkpoint cases, broker authority/deadline/cancellation/tamper
and reconnect cases, persisted-v4-to-v5 and interrupted migration recovery, schema/contract asset
readback, Ruff, and compile smoke. Release-level full-suite and package evidence is published in the applicable release notes
only after the final candidate bytes pass and are frozen.

### Complete installed cutover and public-maintenance closeout — 2026-08-12

Exact source `aabbcfc76c9ba1b2e837fc4c0f01e743fa455479` completed the bounded standalone
AR-LT3 implementation and passed independent review before live cutover. The host-managed supervisor
then migrated additively through schema v5 and read back runtime/dispatcher generation 13 with one
ready owner and no active work. Workspace process-loss, checkpoint restore, stale-handle rejection,
installed-wheel stdio, and duplicate-owner fail-closed canaries passed. The live package remains
`0.3.0a0`; this installed claim does not extend to managed AHC admission, generic exactly-once
effects, activation, or final delivery.

The subsequent `0.3.0a1` maintenance candidate gives the final source a new public identity rather
than rewriting the immutable `v0.3.0a0` tag. It includes the compensation deadline-admission fence:
authority is rechecked after acquiring the SQLite write reservation and again after durable intent
commit immediately before provider invocation. Expiry in either window produces zero provider calls.
The exact candidate repository suite passed 240 tests with one platform-gated Windows skip before
public staging projection.

## 2. Continuation classes

| Class | First supported behavior |
|---|---|
| RLM persisted step boundary | resume from next uncommitted strategy step |
| RLM terminal receipt not projected | commit terminal from receipt |
| Workspace completed-cell checkpoint | restore into a new workspace generation and successor attempt |
| Workspace without compatible checkpoint | needs-user or restart-from-input only when policy allows |
| Broker request with authoritative receipt | reuse receipt; do not call again |
| Broker/effect started without receipt | reconcile or park indeterminate |
| Arbitrary running Python cell | terminate/fence old generation; do not resume mid-cell |
| Native subprocess/socket/lock/open handle | excluded from portable checkpoint |

## 3. Versioned recovery policy

Each durable operation kind declares a policy at admission:

```text
OperationRecoveryPolicyV1
  policy_id + version
  operation_kind
  allowed_decisions[]
  max_successor_attempts
  checkpoint_required
  allow_partial_checkpoint
  environment_compatibility
  broker_uncertainty_policy
  cancellation_policy
  deadline_policy
```

The policy and digest are stored with the operation before acknowledgment. Recovery cannot silently adopt a newer policy.

Initial profiles:

- `rlm.step-boundary.v1`;
- `workspace.checkpoint-boundary.v1`;
- `no-automatic-continuation.v1`.

Unknown policy/version fails closed.

## 4. RLM continuation

The current RLM store persists job state and committed steps; the broker journal persists request/receipt boundaries. AR-LT3 uses them as follows:

1. load the frozen job spec, strategy version, committed steps, usage receipts, and broker traces;
2. verify strategy/package/capability/policy digests;
3. find the highest fully committed step whose broker receipt and step record agree;
4. classify any next broker request:
   - absent: safe to compute next action;
   - started with receipt: reuse receipt and commit the missing step record;
   - started without receipt: reconcile or park;
5. create a successor attempt with prior-attempt and last-step binding;
6. continue at the next strategy action;
7. preserve cumulative host-authoritative budget and deadline;
8. produce one terminal logical-operation result.

A strategy change, missing step, digest mismatch, or reordered action produces `needs_user` or quarantine. Recovery does not regenerate earlier prompts and compare them heuristically.

## 5. Workspace checkpoint continuation

The existing `aar.workspace-checkpoint.v1` manifest is a deterministic JSON-subset snapshot with explicit exclusions and environment fingerprint. AR-LT3 adds a durable operation binding:

```text
OperationCheckpointBindingV1
  operation + attempt
  checkpoint digest
  source workspace/generation/revision
  backend + checkpoint format
  environment digest
  completeness class
  exclusions digest
  artifact references
  creation operation/trace
  recovery policy digest
```

Recovery steps:

1. resolve and digest-verify the manifest/artifacts;
2. verify source operation, attempt, workspace, generation, and revision;
3. apply exact or declared-compatible environment policy;
4. evaluate exclusions against `allow_partial_checkpoint`;
5. create a new workspace generation; never reuse the lost generation;
6. restore supported values;
7. verify the restored snapshot digest/summary;
8. create a successor attempt bound to the new handle;
9. emit checkpoint-selected/restored/successor-started events.

A checkpoint is created only at a completed execution boundary. No checkpoint is inferred from in-memory namespace state after worker loss.

## 6. Environment compatibility

The default is exact compatibility over:

- backend kind and version policy;
- Python implementation/major/minor;
- checkpoint schema;
- required package lock digest;
- platform/architecture when values require it;
- AAR capability digest;
- declared source reconstruction policy.

A relaxed compatibility policy must be versioned, fixture-backed, and explicit. Unsupported values remain listed in exclusions. Pickle is not introduced as the portable format.

## 7. Effect-aware recovery

The durable broker journal is evaluated before any replay:

| Durable observation | Recovery action |
|---|---|
| no broker request persisted | safe to issue under successor attempt |
| request persisted, authoritative success receipt | reuse receipt |
| request persisted, authoritative failure/denial receipt | reuse failure/denial |
| request persisted, host says pending | remain waiting or park under policy |
| request persisted, host says unknown | indeterminate/needs-user; no blind retry |
| host supports explicit idempotent reconcile | reconcile with original host idempotency key |
| compensation available and authorized | propose compensation; host decides |

Model and evidence reads may have more permissive retry policy only when the host declares them side-effect free and idempotent. Subagent submit, artifact put, effect propose, activation, and delivery-shaped capabilities require durable handle/receipt reconciliation.

AAR never treats local timeout, transport close, or missing receipt as proof that an effect did not happen.

## 8. Successor attempt rules

- The logical operation remains unchanged.
- Every successor records prior attempt, recovery decision, policy digest, checkpoint/step boundary, and reason.
- The old attempt is immutable and fenced.
- Attempt count is bounded.
- Cumulative usage and deadlines do not reset unless the host explicitly issues new authority.
- Cancellation requested before recovery prevents new effect work.
- A successor cannot commit unless its lease/generation is current.
- Terminal logical result is create-once; conflicting terminal receipts quarantine the operation.

## 9. Source impact map

Expected modified modules:

- `src/aar/runtime/continuity.py` and registry recovery decisions;
- `src/aar/runtime/dispatcher.py` and handlers;
- `src/aar/runtime/rlm.py` and its store;
- `src/aar/runtime/brokers.py` and broker journal reconciliation;
- `src/aar/runtime/programming.py`;
- `src/aar/runtime/ipython_backend.py` and worker protocol;
- checkpoint/RLM models and canonical schema fixtures;
- MCP status/events/checkpoint projections;
- reference host fake broker reconcile behavior.

Expected tests:

- expanded `tests/test_process_recovery.py` and process probe;
- RLM step-gap and broker-receipt crash matrix;
- checkpoint compatibility/exclusion/revision/generation tests;
- real IPython worker loss and restore tests;
- effect uncertainty and compensation-proposal fixtures;
- package/profile compatibility and bounded artifact disclosure tests.

## 10. Ordered work packages

### LT3-A — policy and checkpoint bindings

Implement versioned recovery-policy and checkpoint-binding models, persistence, fixtures, and strict digest validation.

### LT3-B — RLM step-boundary successor

Resume from committed steps, repair receipt/step projection gaps, preserve budgets, and prove no duplicate broker request.

### LT3-C — workspace checkpoint selection

Select only verified checkpoints for the exact operation/workspace boundary. Add completeness and exclusion policy.

### LT3-D — new-generation restore

Restore plain-Python and IPython checkpoints into successor generations; verify restored snapshot and stale-handle rejection.

### LT3-E — broker/effect reconciliation

Add typed fake-host and adapter hooks for authoritative receipt lookup, pending/unknown state, and optional compensation proposal.

### LT3-F — recovery planner

Produce deterministic recovery decisions from frozen observations. Run it twice against the same snapshot and require identical decision bytes.

### LT3-G — fault injection

Crash at every RLM step, broker call, checkpoint publication, restore, and terminal projection boundary.

### LT3-H — cross-host evidence

Pass the reference host first. Add AHC fixture mappings only after standalone verification; add fresh Codex/Hermes rows for supported/unsupported continuation classes.

## 11. Required scenario matrix

### RLM

1. crash before first broker request;
2. crash after broker request persisted but before receipt;
3. crash after receipt but before step record;
4. crash after step record but before next action;
5. crash after terminal RLM receipt but before logical operation terminal;
6. strategy or capability digest changed before recovery;
7. cumulative budget exhausted across attempts;
8. cancellation arrives before successor claim;
9. late prior-attempt receipt arrives after successor starts.

### Workspace

1. exact compatible checkpoint restore;
2. manifest digest mismatch;
3. environment mismatch;
4. checkpoint with allowed exclusions;
5. checkpoint with policy-forbidden exclusions;
6. worker dies during running cell after prior checkpoint;
7. worker dies during checkpoint publication;
8. restore dies before new generation Ready;
9. stale old workspace handle attempts mutation;
10. current generation closes while recovery races.

### Effects and artifacts

1. authoritative receipt reuse;
2. host pending response across reconnect;
3. host unknown response parks;
4. duplicate artifact put uses original idempotency binding;
5. subagent submit returns durable existing handle;
6. compensation is proposed but not self-authorized;
7. terminal conflict quarantines rather than choosing arrival order.

## 12. Exit criteria

AR-LT3 is verified only when:

- RLM resumes from the next certain step without duplicate broker calls;
- supported workspace checkpoints restore into new generations with exact bindings;
- incompatible/partial checkpoints follow explicit policy;
- arbitrary running-cell/process state remains unsupported and visibly classified;
- broker/effect crash gaps reconcile or park without blind replay;
- successor attempts preserve operation identity, budgets, deadlines, grants, and event lineage;
- recovery decisions are deterministic over frozen observations;
- stale attempt/workspace/lease writes fail closed;
- reference-host evidence passes before any managed-host claim;
- public documentation states checkpoint-boundary continuation, not arbitrary resume or exactly once.

## 13. Rollback and retained evidence

- Keep prior supervisor/handler executable and schema support matrix.
- New checkpoint formats are additive; old manifests remain immutable.
- Never delete a checkpoint or broker receipt used by a recovery decision.
- Failed recovery candidates are quarantined with exact attempt/checkpoint/policy digests.
- Rollback disables new continuation decisions but preserves readable status/events/evidence.
- A database with newer recovery records is not opened by an older binary unless explicit compatibility is proven.

## 14. AHC coordination boundary

AAR recovery decisions are portable computation/runtime evidence. AHC remains authoritative for its durable accepted task, backend identity, model/subagent/effect admission, evidence truth, outbox, and final delivery.

Shared evidence aligns as follows:

- workspace checkpoint/new-generation restore contributes to the optional continuity row under IG-1 with HC-1;
- RLM step-boundary resume and broker budget/effect reconciliation contribute to the optional continuity row under IG-2 with HC-2;
- HC-R0 recovery types are semantic references only; AAR core imports none of them;
- AAR terminal status never closes an AHC source turn or proves user delivery;
- missing/disabled AAR remains a supported AHC mode.
