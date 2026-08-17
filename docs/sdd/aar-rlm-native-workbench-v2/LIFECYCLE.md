# AR-RW Lifecycle, Ownership and Recovery Contract

**Normative status:** required by `README.md` and `CONTRACTS.md`.

## 1. Owners and identities

The runtime has these distinct identities:

- logical operation;
- workbench control revision;
- operation attempt and attempt fence;
- dispatcher generation and dispatch revision;
- lease identity and expiry;
- workspace ID, generation and revision;
- cell execution ID;
- suspension revision;
- caller ticket ID/revision/digest;
- claimant principal/session/adapter generation and claim fence;
- physical external attempt ID;
- child execution ID;
- staged artifact ID;
- cell/finalization manifest digest.

No callback identified only by operation ID may mutate state. Every authoritative mutation validates the full relevant identity and revision set in its write predicate.

## 2. Runtime resource owner

One `RuntimeResourceOwner` owns:

- dispatcher admission and worker registration;
- attempt lease keepers;
- IPython worker pumps;
- caller-ticket sweep and reconciliation actors;
- external receipt callbacks;
- shutdown fencing and store-close order.

Admission and actor registration are serialized with close. Runtime close proceeds:

1. reject new admissions and actor registration;
2. request cancellation or durable transfer of tracked work;
3. revoke and join attempt keepers;
4. stop worker pumps and write-fence workers;
5. drain or candidate-record late callbacks within the close deadline;
6. stop reconcilers and sweepers;
7. close dispatcher and stores;
8. make repeated close a no-op.

No background actor may renew, write or use a repository after step 7.

`RuntimeResourceOwner` exposes one internal interface: `start()`, `register_actor(kind, identity, stop, join)`, `begin_close(deadline)`, `snapshot()`, and idempotent `close()`. `RuntimeResourceOwnerSnapshot` in `aar-rlm-workbench-v1.schema.json` is the exact observable state: owner generation, owner state, admission-open flag, close deadline, actors and last failure. An actor state is `starting | running | stopping | stopped | failed`; terminal `stopped/failed` actors cannot register writes. Store close is forbidden until every actor is terminal and every callback lane is drained or write-fenced.

## 3. Workbench control automaton

`RlmWorkbenchPhase` transitions are:

```text
accepted -> preparing_workspace -> running
running -> waiting_external -> accepted -> running
running -> checkpointing -> running
running -> finalizing -> succeeded
{accepted,preparing_workspace,running,waiting_external,checkpointing,finalizing}
    -> failed | cancelled | timed_out | indeterminate | parked
```

`parked` is a containment phase that projects to legacy `indeterminate`. It is not success and not automatically retryable. Only the current reconciler or an explicit host-authorized successor operation may advance it.

## 4. Caller-work automaton

```text
pending
  -> send_reserved | cancelled_before_send
send_reserved
  -> send_started | cancelled_before_send
send_started
  -> settled_success | settled_failure | outcome_unknown | cancel_requested
outcome_unknown | cancel_requested
  -> settled_success | settled_failure | cancelled_certain | quarantined
send_reserved --claim expiry before send--> pending
```

### 4.1 Claim / send reservation

`aar_broker_work_claim` atomically:

- verifies pending ticket, request/ticket digests, expected revision, current suspension revision, current operation control/cancellation revisions, no authoritative cancellation and the persisted cumulative deadline;
- binds claimant principal/session, adapter ID/generation, claim ID/fence and claim expiry;
- allocates a physical attempt ID and provider/child idempotency identity;
- transitions to `send_reserved`.

A crash in `send_reserved` is certain no-send because the driver MUST NOT call the provider/child before the next transition. After expiry, one claimant can CAS it back to pending or directly replace the reservation. Stale claim mutations fail.

### 4.2 Pre-send cancellation

`aar_broker_work_cancel_before_send` CASes only `pending` or `send_reserved` to `cancelled_before_send`. The same transaction validates expected operation control/cancellation revision, suspension revision, ticket revision/digest and persisted cumulative deadline. A pending cancellation carries no claimant or physical-attempt identity. A reserved cancellation additionally validates the exact unexpired claim ID/fence/expiry and preserves the complete claimant, adapter, attempt and idempotency identity, but requires `send_started_at_unix_ms`, `sent_request_digest`, `sent_at_unix_ms` and external request identity to remain null. Both forms persist a digest-bound local settlement receipt and timestamp.

The transition loses a race with `aar_broker_work_mark_send_started`: if the send-start CAS commits first, pre-send cancellation fails and the ticket follows `cancel_requested`/reconciliation semantics; if cancellation commits first, mark-send-started fails. `cancelled_before_send` is terminal certain-no-send and cannot be reopened or projected as an external cancellation acknowledgement.

### 4.3 Send-start linearization point

Immediately before physical dispatch, the driver calls `aar_broker_work_mark_send_started` with the exact canonical `sent_request_digest`. One `BEGIN IMMEDIATE` transaction reads and validates the current `operation_controls`, `rlm_workbench_jobs`, suspension and ticket rows: expected control revision, expected cancellation revision with cancellation false, expected suspension revision, ticket revision/digest, `send_reserved`, claim ID/fence/expiry, physical-attempt identity, and server time strictly before the claim, request and cumulative deadlines. It then persists the request digest and transitions to `send_started`. The driver sends only after receiving the committed result. Any failed predicate returns the current ticket without dispatch.

A crash after `send_started` is conservatively may-have-sent even when the process died before the actual syscall. `sent_at_unix_ms` records a host-observed dispatch and `provider_or_child_request_id` records an external identity when one is available; neither may be fabricated merely because the pre-send mark committed. Their absence after `send_started` therefore remains indeterminate. Safe retry requires authoritative lookup or a provider/child idempotency guarantee. Blind reclaim is forbidden.

### 4.4 Candidate receipt lane

Provider, child or host callbacks append immutable candidate receipts identified by physical attempt ID and receipt digest. Appending is allowed after lease loss, caller claim loss, cancellation request or successor creation, but appenders cannot alter settled truth.

The current reconciler validates:

- exact ticket/request/physical attempt;
- route/child identity;
- host receipt digest;
- lookup/cancellation evidence;
- existing candidate and settled receipts.

It then CASes authoritative state. Same receipt is idempotent. Conflicting authoritative receipts quarantine. A late receipt can resolve `outcome_unknown`; it cannot overwrite a settled or quarantined state.

## 5. Atomic coordinator operations

These are the only linearization operations. Implementations may use one SQLite transaction or a durable outbox with equivalent proof.

### TX-A — admit job

Atomically create operation identity, workbench job, admitted context/grant/budget/route digests, idempotency binding and initial control revision. Workspace creation occurs after admission and is reconciled idempotently by operation ID.

### TX-B — start cell

CAS current operation/control/attempt/lease/workspace revision. Persist cell ID, source digest, pre-cell checkpoint digest and replay/resume policy before execution.

### TX-C — suspend to caller work

Precondition: a worker emitted one validated broker intent.

Atomically:

- persist suspension record and intent digest;
- create pending caller ticket;
- bind suspension and ticket by operation/suspension revision, ticket ID, method, contract ID, canonical request digest and canonical logical-owner JSON;
- set workbench phase `waiting_external`;
- terminate the current attempt as suspended-certain;
- release/revoke its lease keeper;
- write-fence the worker;
- increment control and suspension revisions;
- enqueue the waiting event.

There is never a visible ticket with a mutable predecessor attempt. A paused worker may remain as cache, but it has no write authority.

### TX-D — caller claim reservation

Implements §4.1 with one CAS.

### TX-E — send-start

Implements §4.3 with the cross-row CAS defined there. It is the physical-dispatch linearization boundary. Fault barriers are required before the transaction, after its durable commit but before physical dispatch, and after physical dispatch before host observation. Cancellation wins before commit; after commit the outcome is conservatively may-have-sent and must reconcile.

### TX-F — append and settle receipt

Appending the immutable candidate receipt and settling authoritative state are separate. Settlement CASes current ticket revision and current reconciliation owner. Settlement creates a durable successor-admission outbox row keyed by `(operation, suspension_revision)`.

### TX-G1 — prepare successor rebind

From one pending `rlm_workbench_successor_outbox` row, create at most one successor attempt for the next rebind generation, CAS the outbox to `prepared`, and insert one `rlm_workbench_rebind_transfers` row. The row binds token digest, outbox and settlement digests, prior/successor attempt IDs and fences, prior/successor authority generations, worker process identity, workspace generation/revision, cell and suspension revisions, control/cancellation revisions, cumulative deadline and expiry. `rebind_prepare` is sent only after this row commits. Prepare write-fences the worker's predecessor grant; it does not authorize resumption.

### TX-G2 — commit successor authority

One transaction proves the predecessor is fenced, operation control/cancellation revisions are current, the deadline and token have not expired, the transfer and outbox remain prepared, and the exact successor attempt/fence and worker process are current. It then CASes `rlm_workbench_attempt_authority` from predecessor to successor, updates `operation_dispatch.current_attempt_no`, changes the transfer to `committed`, and consumes the outbox. `rebind_commit` is sent only after this transaction commits. Every subsequent worker frame must match the current authority row and a `committed | consumed` transfer for the same token; predecessor frames fail closed.

### TX-G3 — acknowledge or burn committed authority

A matching `rebind_ack` consumes the transfer as `worker_ack`. If the exact worker process is lost after durable commit, restart first fences the successor attempt and process, then consumes the transfer as `recovery_fenced_loss` and starts checkpoint-based recovery under a later authority generation. A committed transfer can never become `aborted`; abort is legal only from `prepared`.

### TX-H — commit cell

Persist a `CellCommitManifest` binding source, result, checkpoint, pre/post workspace revisions and artifact stages. Promotion and projection follow §9. No stale attempt can write: the repository mutation itself carries a CAS predicate over current attempt/fence/control/workspace/cell identity.

### TX-I — finalize

One coordinator receives planner `finalize` or worker `completion_intent` as non-authoritative proposals. It validates output, child policy, usage, artifact bindings and workspace disposition, then persists a `FinalizationManifest`.

The finalization CAS is the single logical commit point for terminal result, operation state, artifact authority, usage and workspace disposition. Cancellation before this CAS wins; cancellation after success is a no-op. A crash after the CAS reprojects the same result.

## 6. Workspace execution and external calls

### 6.1 Normal live resume

The typed Python facade is synchronous-looking. When a broker call has no receipt:

1. the worker emits a broker intent and pauses;
2. TX-C durably suspends the operation;
3. the host settles the caller ticket;
4. TX-G1 creates a durable prepared transfer and sends `rebind_prepare`;
5. TX-G2 atomically installs successor authority and sends `rebind_commit` with the settled receipt;
6. the exact worker resumes once and emits `rebind_ack`; TX-G3 records acknowledgement.

Correctness does not depend on the worker remaining alive.

Live-stack resume uses `SuccessorRebindToken` and the `rebind_prepare -> rebind_commit | rebind_abort` plus `rebind_ack` frames from `aar-workspace-broker-frame-v1.schema.json`. The token is a projection of the durable transfer row, not authority by itself. Duplicate, expired, cross-worker, stale-fence or non-current-authority tokens fail without resuming code.

Restart classification is deterministic:

- before TX-G2 commit: the row remains `prepared`; restart may retry the same commit or abort and return the outbox to pending after proving the prepared worker has not resumed;
- after TX-G2 commit before delivery: successor authority is already current; restart redelivers the same digest-bound commit;
- after delivery before acknowledgement: restart queries the exact worker process; an applied token is acknowledged idempotently, an unapplied token is redelivered, and an unavailable worker is fenced then classified `recovery_fenced_loss`;
- after TX-G3: replay returns the existing consumed outcome and never resumes a second owner.

### 6.2 No automatic replay of arbitrary Python

AR-RW v1 does not replay an arbitrary interrupted cell from source. The current trusted-local IPython profile cannot prove absence of raw filesystem, network, subprocess, time, randomness, native-extension or excluded-live-object effects before suspension. Model-supplied `replay_policy` is not authority.

If the paused worker is lost:

- restore the pre-cell checkpoint into a successor workspace generation;
- retain certain broker receipts as evidence;
- mark the interrupted cell `lost_before_commit`;
- issue a bounded recovery-planner call using the exact `RecoveryPlannerInput` schema; it contains objective-bound operation identity, source/checkpoint digests, suspension revision, committed events/artifacts, settled receipt digests, loss reason and remaining budgets;
- execute a new continuation cell with a new cell ID.

The lost cell is never automatically re-executed. Recovery planner failure or required unportable state parks the job. A future managed-restricted profile may add enforceable deterministic replay under a new negotiated contract; it is not claimed here.

### 6.3 Raw local effects

In `trusted_local`, raw Python effects can physically occur under host-account permissions. They are outside AAR authority, receipts, compensation and recovery. Skills instruct model-written Python to use typed brokers. Managed AHC admission requires an independently verified restricted execution profile; AR-RW v1 does not invent one.

## 7. Planner and correction calls

Planner, correction, recovery and finalizer calls use the planner logical-owner variant and the same caller automaton. They do not require a cell ID. Their structured result is validated before any workspace mutation.

A malformed directive can consume a bounded correction call. The correction has a new logical owner step index. Exhaustion is a certain contract failure.

## 8. Subagent lifecycle

`subagent.submit.v2` and `subagent.result.v2` are separate durable calls.

A child execution record contains host idempotency identity, adapter generation, accepted-handle receipt, current state, result/artifact receipt revisions and cancel acknowledgement.

The parent cannot succeed while a required child is nonterminal. Optional detached children are excluded from v1. Parent cancellation requests child cancellation. If the host cannot acknowledge a terminal child state, the parent parks or becomes indeterminate; it does not pretend cancellation.

Recursive depth and cumulative child budgets are charged at admission and each submit.

## 9. Artifact and event commit semantics

Workers emit typed `artifact_stage_intent` proposals that bind a worker buffer, digest, size and logical artifact identity. Only the supervisor, after validating current attempt authority and reading the exact bounded bytes, may create an `rlm_workbench_artifact_stages` row. Staged bytes remain invisible. Failed, suspended, cancelled and lost cells do not publish final artifact bindings.

`CellCommitManifest` is the authority for a successful cell's checkpoint and staged artifacts. Promotion can occur after restart only when that manifest is current and no terminal cancellation predated it. Promotion is idempotent.

Final user-visible artifact bindings are authorized by `FinalizationManifest`. This is the product visibility point. Intermediate committed cell artifacts may be visible to the running workbench by stage ID but are not returned as final product artifacts until finalization.

Progress/display events are provisional until their cell commits. Event IDs derive from operation, cell and frame sequence. Repeated provisional frames are deduplicated; interrupted-cell events are marked discarded and never projected as committed history.

## 10. Lease keeper

A service-managed attempt starts a keeper only through `RuntimeResourceOwner`. Keeper registration and attempt transition to running are serialized. Each renewal CASes:

- operation still running;
- exact attempt and attempt fence;
- dispatcher generation and dispatch revision;
- lease identity;
- control revision;
- cumulative deadline.

Suspend, terminal transition, ownership loss or runtime close revokes and joins the keeper. Keeper failure becomes an explicit attempt failure/park event before lease expiry; it cannot fail silently.

Caller-delegated waiting has no attempt lease. A durable sweeper scans waiting deadlines at startup and periodically; status/read also performs lazy expiry reconciliation.

## 11. Deadline and cancellation

Deadline expiry stops new physical sends. For `pending | send_reserved`, the sweeper attempts the same fenced `cancelled_before_send` CAS and records certain-no-send; it does not merely drop a reservation while leaving required work unresolved. For `send_started`, expiry requests cancellation/lookup and remains indeterminate until terminal acknowledgement or reconciliation evidence exists.

Cancellation follows the same rule. `cancel_requested` is not `cancelled_certain`. Terminal timeout/cancel is emitted only after known no-send or acknowledged external termination. Late matching receipts enter the candidate lane.

## 12. Attempt fences and inner writes

A read-then-write fence check is insufficient. RLM steps, broker journal writes, artifact stages, subagent rows, workspace commits, outbox rows and finalization rows include current fence/revision predicates in the mutation transaction. A stale attempt may append a candidate receipt for its physical external attempt, but cannot publish authoritative state.

## 13. Shutdown and restart

On startup, the owner reconciles:

- running attempts with expired/missing leases;
- waiting tickets and deadlines;
- send-started tickets without settled receipts;
- successor outboxes;
- staged artifacts and cell manifests;
- finalization manifests not fully projected;
- operation-scoped workspace generations and checkpoints;
- required live children.

No frontend must be connected for reconciliation to proceed.

On shutdown, §2 ordering applies. Resistant provider/child callbacks are write-fenced and may only append candidate receipts after close if the durable callback ingress remains separately owned; otherwise callback ingress is stopped before stores close.

## 14. Acceptance and fault evidence

`fault-matrix.json` contains atomic barriers. Each required fault case produces evidence bound to one candidate manifest and parent acceptance ID. A broad acceptance row is not satisfied by one representative variant; all required child fault cases must pass.

Mutation probes must demonstrate that removing the specific fence, send-start boundary, outbox uniqueness, artifact staging or finalization CAS makes the corresponding test fail.

The normative matrix never stores mutable pass state. `aar.acceptance-results.v1` stores candidate-bound evidence and distinguishes `not_verified`, `implementation_verified` and `live_qualified`.
