# Lifecycle and Temporal Ownership

## 1. Safety properties

1. No ordinary server start auto-migrates a v5 database.
2. Invalid activation bytes fail in read-only preflight before host construction, runtime-generation allocation, listener/discovery publication, grant-issuer configuration, or operation creation; no workbench mutation grant exists before exact v6/profile/adapter activation.
3. No root planner or cell broker physical call occurs before durable send-start.
4. A possibly sent request is never blindly replayed.
5. A stale runtime, attempt, claim, adapter generation, or profile cannot commit a newer result.
6. Result success requires terminal reconciliation, output contract pass, artifact commit, budget consistency, and workspace disposition.
7. Capability output reports exact reference factories as frozen-v8 configured/reference-only truth, but admission never treats a reference, fake, stale, or uninstantiated adapter as usable.

Liveness is conditional on an authorized caller servicing tickets, provider/gateway progress, bounded SQLite progress, and supervisor scheduling fairness. Safety does not depend on those fairness assumptions.

## 2. Cutover state machine

```text
ABSENT
  -> PLANNED
  -> PREPARED
  -> DB_COMMITTING
  -> DB_COMMITTED
  -> VERIFIED
  -> COMMITTED

PLANNED/PREPARED -> ABORTED_BEFORE_DB_COMMIT
DB_COMMITTING    -> RECOVERY_REQUIRED
DB_COMMITTED     -> RECOVERY_REQUIRED | VERIFIED
COMMITTED        -> forward-only operational state
```

Durable evidence is external-to-DB immutable strict prepared/terminal epoch markers plus append-only per-profile activation history, the derived current pointer, and the in-DB v6 attestation.

### Linearization points

| Operation | Final authorizing check `C_final` | First mutation `M` / commit point |
|---|---|---|
| Prepare cutover | Fresh plan, owner absent, DB identity/digest unchanged, verified snapshot and epoch unused | Atomic publication of strict cutover `aar.operator-prepared-marker.v1` after its self digest is computed |
| Apply migration | Prepared marker/intent/candidate/database revalidated under exclusive lock | Commit of one SQLite transaction containing DDL plus exact v6 attestation |
| Commit activation generation | DB/attestation/final profile match prepared intent; complete history/marker scan has one exact tip; proposed generation is strictly greater and links to that tip | Exclusive-create, fsync, atomic publication and parent-fsync of the immutable history record; this is the generation commit linearization point. Derived `current.json` replacement follows. |
| Commit cutover | v6/attestation/integrity/profile/history tip/current pointer match prepared intent | Compute receipt, wrap it downstream, then atomically publish immutable `aar.operator-terminal-marker.v1` as `committed.json` |
| Prepare restore | Exact cutover prepared marker/snapshot/target/sidecars plus all frontier booleans | Atomic publication of strict restore `aar.operator-prepared-marker.v1` |
| Restore snapshot | Exact restore prepared marker; no DB commit or separately proven no post-cutover writes | Atomic operator-owned database replacement while runtime remains stopped; receipt/terminal marker follows verified readback |
| Activate supervisor | v6/final-profile/intent/candidate/adapters/grants all match | Publication of generation-unique Ready/discovery record |

A separate precheck followed by an unlocked mutation is forbidden. The cutover/runtime-initialize exclusive lock is acquired before the final prior-authority/generation check and retained through DB commit, post-check, activation-authority CAS, and terminal marker publication. Supervisor startup acquires the same lock in shared mode before its final activation check and retains it for its lifetime; the predecessor runtime-owner identity separately prevents two supervisors. Lock loss before a terminal marker is `RECOVERY_REQUIRED`, never implicit abort/success.

## 3. Activation state

Activation status is derived from observed owners and artifacts:

```text
UNCONFIGURED
MIGRATION_REQUIRED
PROFILE_INVALID
PROFILE_VERIFIED
STARTING
ACTIVE
DEGRADED
RECOVERY_REQUIRED
```

Only `ACTIVE` may publish mutation grants. `ACTIVE` additionally requires one valid append-only activation-history chain whose tip is bound to the loaded intent/final profile/attestation/generation, exact equality between that tip and derived `current.json`, and matching committed epoch markers. `DEGRADED` preserves existing durable operations for status/reconcile/cancel as policy allows but does not admit new work. Profile/adapter/route/history drift forces a new strictly larger activation generation plus fresh runtime generation; hot mutation of an active profile is forbidden in v1.

## 4. Root planner caller-delegated lifecycle

```text
RUNNING
  -> PLANNER_TICKET_PENDING
  -> SEND_RESERVED
  -> SEND_STARTED
  -> SETTLED_SUCCESS | SETTLED_FAILURE | OUTCOME_UNKNOWN
  -> RECONCILING
  -> DIRECTIVE_READY | CORRECTION_REQUIRED | TERMINAL_FAILURE | QUARANTINED | CANCELLED_CERTAIN | DEADLINE_CERTAIN
  -> RUNNING | FINALIZING | TERMINAL
```

### Required ordering

1. Persist planner logical owner, request, route, deadline, budget snapshot, suspension revision, and ticket.
2. Commit workbench phase `waiting_external` and release the attempt/worker ownership allowed by predecessor rules.
3. Claim performs CAS from `pending` to `send_reserved` and binds claimant/adapter/physical-attempt identities.
4. `mark_send_started` is the conservative may-have-sent point and MUST precede physical dispatch.
5. Successful model commit validates the required strict `model_response` against the current ticket/request/route and observation digests, then records model journal, candidate, settlement/outbox and command receipt in one SQLite transaction. Non-model or non-success observations require `model_response=null`. A late provider callback uses the sealed journal-plus-candidate transaction before reconciliation.
6. A root-planner successor maps the nullable-cell suspension to its predecessor through one exact waiting-external operation event, then uses the frozen-v6 outbox CAS: released predecessor lease, exact settlement/control/deadline, `pending→prepared` for a current attempt, or generation-advancing `prepared→prepared` takeover only after the stored successor lease and dispatch authority are dead.
7. The exact current successor selects exactly one `CONTRACTS.md §7` outcome and commits `prepared→consumed` plus valid directive, correction, certain failure/cancel/deadline and its event/projection in one registry transaction. Outcome unknown does not prepare or consume. Before commit a crashed prepared owner can be fenced/taken over; after commit the authoritative projection exists. Planner flow never touches cell-bound attempt-authority/rebind-transfer rows; a real cell authority begins only inside a valid directive transaction.

A caller-delegated `execute` is admitted only with frozen-v8 `start_only=true`. `start_only=false` is rejected before operation creation without exception; v1 does not define a duplex-host escape hatch or alter the frozen request schema.

## 5. Planner logical ownership

Planner phases are independent logical calls. Identity reuse across changed input is forbidden.

| Phase | Input authority | Allowed directive | Recovery rule |
|---|---|---|---|
| initial | Job objective, profile/grants, empty/current snapshot | execute cell / finalize / abstain | No hidden retry; correction gets a new owner/ordinal. |
| correction | Invalid prior response digest and bounded validation errors | valid replacement directive / abstain | Prior spend remains charged. |
| recovery | Certain pre-cell checkpoint, failed cell digest, settled external receipts | new continuation cell only | Re-emitting failed source digest is conflict. |
| finalizer | Current authoritative snapshot, artifacts, usage, children, workspace state | finalize / abstain | Cannot hide unresolved tickets or invalid output. |

## 6. Attempt, lease, and waiting rules

- Workbench execution attempts retain their existing renewable lease only while performing owned local work.
- `waiting_external` has no active execution-attempt lease merely to represent waiting.
- Caller claims have their own bounded expiry and generation/claim fences.
- An internal service-managed provider call requires an independent lease keeper and is not inferred from the caller-delegated design.
- Expired caller claim before send may be reclaimed through CAS.
- Expired claim after send-start is not replay authority; it becomes outcome unknown until reconciled.

## 7. Cancellation

| Point | Required result |
|---|---|
| Before operation acceptance | Certain no operation/no provider call. |
| Ticket pending | `cancelled_before_send`; certain no provider call; no planner successor outbox. Existing outer cancellation/finalization authority observes the cancelled suspension. |
| Send reserved, not started | CAS cancellation; certain no provider call. |
| Send started | Request cancellation if supported; otherwise `cancel_requested`/unknown, then lookup or quarantine. |
| Candidate receipt exists | Reconciler decides whether provider terminal evidence or cancellation authority wins. |
| Known settled/validated directive, before directive transaction | Newer cancellation revision causes atomic outbox consumption into certain cancellation; directive/workspace mutation is not applied. |
| Outcome unknown after send-start | Preserve ticket/suspension with no successor outbox and reconcile; cancellation does not authorize terminal certainty or replay. |
| Finalization CAS | Exactly one of cancellation or success wins under current revision/fence. |

Cancellation never deletes receipts or rewrites prior usage.

## 8. Timeout

The one cumulative job deadline covers:

- queue/admission;
- planner and correction calls;
- cell execution;
- caller claim/send/commit;
- cancellation/cleanup/reconciliation;
- finalization.

Each boundary checks the same absolute deadline. Expiry before send uses the existing cancellation/finalization authority and creates no planner successor outbox. Expiry after an outbox-eligible known settlement but before directive commit projects a certain deadline terminal row through the fenced successor transaction. A timeout after send-start with no terminal evidence is `indeterminate`; it creates no outbox and authorizes only lookup/reconcile, never certain no-spend or replay. Cleanup has a bounded sub-budget and cannot extend total wall time silently.

## 9. Crash/recovery matrix

| Crash point | Authority on restart | Required action |
|---|---|---|
| Snapshot complete, before prepared marker | Old DB/profile; no cutover authority | Exact snapshot/plan/source/sidecar bytes may be adopted under lock; mismatch/ambiguity is recovery-required and the final snapshot is not overwritten/deleted. |
| After prepared, before DB transaction | Prepared intent; old DB | Replacement operator acquires exclusive lock, verifies owner/plan/DB tuple, then explicitly aborts or applies; supervisor remains refused. |
| During SQLite transaction | SQLite commit state plus prepared marker | Replacement operator acquires exclusive lock and runs status/reconcile; never rerun blindly. |
| After DB commit, before history publication | In-DB attestation plus prepared prior-tip precondition are evidence; no new activation generation is committed yet | Explicit matching reconcile verifies DB/profile and unchanged history tip, publishes the one exact history successor, repairs current, and appends terminal marker; never lower/reuse. |
| After history publication, before current-pointer replace | Immutable history tip is generation authority; current is absent/stale | Explicit reconcile validates the unbranched chain and committed markers, then replaces current with the exact tip; no new history record. |
| After current-pointer replace, before external committed marker | History tip and current agree; epoch is incomplete | Explicit reconcile verifies DB/profile/history/current equality, computes the receipt, wraps it once in the terminal marker and publishes that marker; never replay history CAS or overwrite different bytes. |
| Cold start after history/current/marker deletion, fork, or rollback | Scan result conflicts or lacks a committed referenced record | `ACTIVATION_HISTORY_CONFLICT`/recovery-required; no Ready, grant issuer, runtime mutation, pointer repair across a fork, or silent downgrade. |
| After active profile load, before Ready publication | No client-visible active generation; shared lock/runtime owner still required | Restart with same exact profile after ownership and unmatched-marker checks. |
| After planner ticket, before claim | Pending ticket | Reclaim normally. |
| After claim, before send-start | Send-reserved | Expiry/cancel/reclaim through CAS. |
| After send-start, before provider receipt | May have spent | Lookup/reconcile/quarantine; no replay. |
| After candidate receipt, before settlement | Candidate is evidence, not authority | Current reconciler settles. |
| After `settled_success`, `settled_failure`, or `cancelled_certain`, before successor dispatch | Settled receipt and pending outbox | Idempotently schedule/claim one successor for the exact success/correction/certain-terminal outcome. |
| After `cancelled_before_send` | Cancelled suspension; no outbox | Existing outer cancellation/finalization CAS completes under current cancellation revision; no planner successor. |
| After `cancel_requested`, `outcome_unknown`, or `quarantined` | Caller-work uncertainty plus ticket/suspension; no outbox | Reconcile/quarantine only; no successor prepare/consume or replacement ticket. |
| Late original writer after successor authority | Stale fence | Reject without mutating current state. |

## 10. Atomic coverage rows

Implementation acceptance MUST include at least:

- first per-profile activation with absent history, equal/lower generation rejection, strict higher successor and changed-prior-tip conflict across preserved-v5 cutover and empty-runtime v5-bootstrap cutover;
- profile equal-value ABA with changed activation generation and append-only authority chain;
- crash immediately before/after history publication, current-pointer replacement and terminal marker;
- history fork, missing chain member, duplicate generation, current rollback, missing committed-marker reference and derived-pointer repair;
- adapter ID equal-value ABA with changed generation;
- principal/session mismatch and unavailable identity observation;
- owner PID reuse/replacement and stale supervisor discovery;
- lock loss between `C_final` and `M` for cutover and restore;
- database/WAL drift after plan;
- intent/final-profile/contract/wheel digest drift after plan;
- duplicate idempotency key with changed planner request;
- stale ticket revision and claim fence;
- cancellation/finalization race;
- deadline/receipt race;
- candidate receipt/late quarantine writer race;
- planner successor duplicate/predecessor-event mismatch, live-owner takeover refusal, dead-owner prepared→prepared fencing, every valid-directive/correction/certain-failure/cancel/deadline/unknown row, crash before/after each atomic consume projection, and zero access to cell-bound authority/rebind tables;
- physical request lookup disagreement;
- route/effort/fallback drift;
- usage missing/estimated/total mismatch.

Every test asserts both the returned classification and absence of the wrong mutation/provider send/success state.

## 11. Terminal result

Success requires one current finalization CAS after:

- all required caller work settled;
- planner directive and final output schemas pass;
- route/usage lineage reconciles;
- cumulative budgets include retries, correction, recovery, cancelled/discarded spend;
- required children are terminal;
- artifacts are committed by the predecessor finalization rules;
- workspace terminal checkpoint/disposition is recorded;
- current runtime/attempt/profile/control/cancellation/finalization fences match.

A terminal receipt is append-only evidence. Later richer provider telemetry may supersede an unknown field through a versioned reconciliation record, never by rewriting history.
