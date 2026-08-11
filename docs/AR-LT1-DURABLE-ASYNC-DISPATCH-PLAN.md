# AR-LT1 — Durable Async Dispatch and Reconnect Implementation Plan

**Status:** verified for durable `rlm.execute` in the standalone single-runtime reference host
**Updated:** 2026-08-11
**Owning workstream:** [LONG-TASK-CONTINUITY-PLAN.md](LONG-TASK-CONTINUITY-PLAN.md)
**Cross-project coordination:** maintained in the separate AHC-by-AAR planning workspace; not normative to AAR core
**Initial durable workload:** `rlm.execute` under the standalone reference host

## 1. Objective

Turn persisted accepted intent into real asynchronous execution owned by a durable dispatcher rather than the originating MCP request. A client may disconnect, reconnect under valid authority, read events/status/result/artifacts, or request cancellation without owning the worker lifetime.

AR-LT1 remains a single-runtime design. Process separation of the durable supervisor from ephemeral MCP frontends belongs to AR-LT2.

## 2. Scope and non-goals

In scope:

- generic durable dispatch substrate;
- atomic claim and lease acquisition;
- one active attempt per logical operation;
- `rlm.execute` as the first enabled durable handler;
- reconnectable status, event pages, terminal result, artifacts, and cancellation;
- dispatcher restart pickup inside a new runtime generation;
- bounded fairness, concurrency, and backpressure;
- explicit capability matrix for operation kinds.

Out of scope:

- transparent survival of the entire `aar-mcp` process;
- arbitrary workspace cell replay;
- checkpoint restore;
- multi-node dispatch;
- exactly-once external effects;
- AHC final delivery or durable task ownership.

## 3. Runtime architecture

```text
MCP/direct adapter request
       |
       | persist intent + return operation handle
       v
OperationRegistry / DispatchQueue
       |
       | atomic claim + lease
       v
DurableDispatcher
       |
       +--> RlmOperationHandler
       +--> future WorkspaceOperationHandler
       |
       v
terminal receipt / indeterminate classification
```

The adapter never calls the RLM engine directly for an operation advertised as durable. `start_only=true` means persist and return immediately. A non-`start_only` call uses the same durable path and waits on operation state until its request wait deadline; disconnecting that wait does not cancel execution.

## 4. Dispatcher contract

Introduce an internal handler protocol:

```text
OperationHandler
  kind
  validate_persisted_request(request, payload)
  run(attempt_context) -> terminal receipt or typed interruption
  request_cancel(attempt_context)
  reconcile(attempt_context) -> recovery observation
```

`AttemptContext` contains only validated persisted request/payload, operation/attempt identity, current lease fence, deadlines, grants, artifact sink, and broker facade. It does not contain an MCP connection object.

Initial handler registry:

| Operation kind | AR-LT1 mode |
|---|---|
| `rlm.execute` | durable async enabled |
| read-only status/events | direct read |
| workspace create/execute/checkpoint/restore | existing synchronous path; capability says not durable |
| asset import/materialization | existing path; no automatic dispatch |
| broker effects | never dispatched as top-level blind replay |

The generic tables and handler interface must support later operation kinds without claiming them now.

## 5. Atomic claim and lease algorithm

Under one SQLite transaction:

1. select the oldest eligible accepted operation whose deadline is not expired;
2. verify no live current attempt or unexpired lease exists;
3. create the next `attempt_no` and immutable `attempt_id`;
4. increment the lease epoch and bind current runtime/dispatcher generation;
5. write `attempt_claimed` and `execution_started` events;
6. commit before invoking the handler.

The dispatcher heartbeats at a bounded interval shorter than the lease duration. A failed heartbeat stops new broker calls and terminal writes. A receipt commits only through a compare-and-set over operation, attempt, runtime generation, dispatcher generation, lease epoch, and expected record revision.

Suggested first defaults, subject to AR-LT0 approval:

- lease duration: 30 seconds;
- heartbeat interval: 10 seconds;
- claim batch: 1–8;
- per-runtime concurrency: 2;
- event-page default/max: 100/500;
- idle poll backoff: 100 ms to 2 seconds with wake notification.

These are capability limits, not hidden constants.

## 6. Dispatcher lifecycle

```text
created -> ready -> dispatching -> draining -> stopped
                       |
                       +-> degraded / reconcile-required
```

Startup:

- complete schema migration;
- increment runtime and dispatcher generation;
- classify accepted and prior-generation attempts;
- enqueue immediately safe accepted work;
- leave uncertain running work for recovery policy;
- start bounded claim loop only after Ready.

Shutdown:

- stop claiming;
- persist drain requested;
- ask active handlers to yield/cancel according to policy;
- release only leases known to be stopped;
- leave unresolved owners expired/indeterminate rather than marking failure.

## 7. Reconnectable read surface

Public continuity tools:

- `aar_operation_status` — current logical snapshot and optional negotiated continuity snapshot;
- `aar_operation_events(operation_id, after_sequence, limit)` — bounded cursor page;
- existing result/artifact resolution tools;
- `aar_operation_cancel` — durable cancellation request;
- `aar_operation_reconcile` — explicit reconciliation mutation.

Read tools must perform zero writes. Long polling, if added later, is adapter-specific and must not change core semantics.

Status includes:

- operation state/certainty/revision;
- current or last attempt summary;
- cancellation request status;
- last event sequence;
- result/artifact/checkpoint references;
- reconciliation required and reason code;
- capability limitations.

## 8. Cancellation behavior

1. persist cancellation request and event;
2. notify current handler if one is attached;
3. handler stops starting new broker calls;
4. cooperative stop is preferred;
5. escalation fences the attempt before terminating a worker;
6. terminal `cancelled` is committed only by the current lease owner or recovery transaction;
7. late receipts from the cancelled/stale attempt are retained as rejected evidence, not applied.

Repeated cancel is idempotent. Cancellation of already terminal work returns the terminal snapshot.

## 9. Deadline and retry behavior

- Request wait deadline and operation execution deadline are separate.
- A client wait timeout never changes operation state.
- Operation deadline is persisted from the accepted request.
- Accepted work whose deadline expires before claim becomes terminal timed-out without an attempt.
- An active attempt that crosses its deadline receives durable cancel intent and is fenced on escalation.
- AR-LT1 does not automatically create successor attempts after uncertain broker/effect boundaries; it parks for reconcile.

## 10. Source impact map

Expected new modules:

- `src/aar/runtime/dispatcher.py`;
- `src/aar/runtime/handlers.py` or a small handler package;
- `src/aar/runtime/continuity.py` for shared attempt/lease decisions.

Expected modified modules:

- `src/aar/runtime/registry.py`;
- `src/aar/runtime/reference_host.py`;
- `src/aar/runtime/rlm.py`;
- `src/aar/runtime/brokers.py`;
- `src/aar/mcp/models.py`;
- `src/aar/mcp/server.py`;
- package/profile capability snapshots.

Expected tests:

- new `tests/test_dispatcher.py`;
- expanded `tests/test_mcp_server.py`;
- expanded `tests/test_process_recovery.py` and `tests/process_probe.py`;
- `tests/test_rlm.py`, `tests/test_broker_facade.py`, `tests/test_ar23_regressions.py`;
- compatibility smoke for Codex and Hermes.

## 11. Ordered work packages

### LT1-A — dispatcher skeleton and disabled capability

Construct and stop the dispatcher with no eligible handlers. Prove startup/shutdown and generation records without changing v5 behavior.

### LT1-B — atomic queue/claim/lease

Implement eligibility queries, claims, heartbeats, CAS terminal writes, stale-owner rejection, deadline expiration, and bounded fairness.

### LT1-C — RLM durable handler

Reconstruct `RlmJobSpec` and `RequestEnvelope` only from stored validated bytes. Route execution through existing `RlmEngine` and broker journal.

### LT1-D — reconnect/status/event page

Expose the negotiated event tool and continuity snapshot. Prove zero-write reads and stable cursor progression across new client connections.

### LT1-E — durable cancellation

Separate cancel intent from terminal state, add handler notification, escalation, and stale-receipt evidence.

### LT1-F — unified submit-and-wait

Make synchronous RLM calls submit to the durable path and wait. Keep a feature flag until output/usage/error equivalence with the old inline path is proven.

### LT1-G — restart pickup

Kill the dispatcher between accept/claim, claim/run, broker receipt/step receipt, and terminal receipt/operation terminal. Classify each boundary without duplicate broker work.

### LT1-H — compatibility and canary

Enable only in the reference host profile, then fresh Codex/Hermes clients. Do not advertise durable workspace execution.

## 12. Required scenario matrix

1. 30–60 minute deterministic RLM job with periodic events;
2. client disconnect immediately after accepted acknowledgment;
3. client reconnect with the same valid authority and reads all events/result;
4. unrelated or unauthorized session is denied;
5. duplicate submit during running returns the same operation;
6. dispatcher process loss before claim leaves accepted work eligible;
7. loss after claim but before broker call creates a safe successor;
8. loss after broker started/no receipt parks indeterminate;
9. loss after durable broker receipt reuses receipt and does not call twice;
10. cancellation before claim, during RLM step, and racing terminal receipt;
11. old attempt late success is rejected after successor claim;
12. event pagination over more than 500 events has no gaps or duplicates;
13. one hot operation cannot starve another beyond the declared policy;
14. database busy/backpressure is visible and bounded;
15. closing the MCP client does not invoke dispatcher shutdown.

## 13. Exit criteria

AR-LT1 is verified only when:

- persisted accepted RLM work is actually claimed and executed by the dispatcher;
- a disconnected client can later read status/events/result/artifacts;
- same-payload replay and cancel are idempotent;
- stale owners cannot heartbeat, broker, or commit terminal state;
- crash windows are classified by fault injection;
- event reads are bounded and zero-write;
- old inline RLM and new submit/wait outputs are equivalent on frozen fixtures;
- ordinary v5 clients and non-durable operation kinds remain compatible;
- no claim is made that the AAR supervisor itself survives process death;
- no AHC queue, delivery, or authority state is copied into AAR.

## 14. Rollback and feature control

- Default continuity dispatch to disabled until the reference-host candidate passes.
- Keep old inline RLM behind a temporary compatibility flag through one candidate cycle.
- Do not run old and new execution paths for the same operation.
- On rollback, stop claims, drain/fence active attempts, preserve database evidence, and use the pre-migration database copy only if reverting to an old binary.
- A failed candidate is quarantined; accepted work remains visible for manual reconcile.

## 15. AHC coordination boundary

AHC may later map one AHC durable task to one AAR logical operation and treat AAR attempts as subordinate execution evidence. AHC remains owner of accepted user work, backend/session identity, effects, outbox, and final delivery.

AR-LT1 does not block HC-R0 or HC-0. It contributes an optional continuity fixture profile. A shared continuity claim first belongs under IG-1 after HC-1 proves exact identity/generation mapping, late-result fencing, optional degradation, and no duplicate final result.

## 16. Verified implementation closeout

AR-LT1 is verified on the exact standalone source and wheel candidates recorded in the append-only
WAL. The implementation and process evidence prove:

- one durable `rlm.execute` queue with atomic claim, attempt creation, lease acquisition, heartbeat,
  terminal commit, and full stale-owner fencing;
- `start_only` and submit-and-wait using the same dispatcher-owned execution path, independent of MCP
  request lifetime;
- restart pickup under a new runtime generation with stable logical operation identity and a fenced
  successor attempt;
- reuse of an authoritative persisted broker receipt without a second broker call, while a started
  call without a receipt remains indeterminate and reconciliation-required;
- durable queued and running cancellation semantics, reconnectable status/result, ordered bounded
  events, and zero-write reads;
- bounded two-worker dispatch, including an idle-poll regression that prevents worker wake-up
  ping-pong;
- an OS-backed database ownership lock that rejects a second live `aar-mcp` process before it can
  steal the runtime generation and releases automatically after process death;
- exact-wheel compatibility and fresh installed Hermes gateway readback of v6, 30 tools, and
  `aar-operations` `0.7.0`.

This remains a single-runtime design. AR-LT1 does not separate a durable supervisor from ephemeral
frontends, restore arbitrary live workspace state, replay ambiguous external effects, or own AHC task,
delivery, activation, or provider authority. AR-LT2 is the next planned phase and has not started.
