# AR-LT0 — Continuity Contract and Failure-Model Implementation Plan

**Status:** verified at the standalone contract, migration, registry, and MCP v6 boundary
**Updated:** 2026-08-11
**Owning workstream:** [LONG-TASK-CONTINUITY-PLAN.md](LONG-TASK-CONTINUITY-PLAN.md)
**Cross-project coordination:** maintained in the separate AHC-by-AAR planning workspace; not normative to AAR core
**Source baseline inspected:** `de3e19697c124b93dcbfb516df3ad79f4198ed8a`
**Start dependency:** satisfied; AR-0 was verified and AR-LT0 was explicitly approved

## 1. Objective

Freeze the transport-neutral contract and executable failure model needed for durable long operations before a background dispatcher or persistent service owns real work.

AR-LT0 is complete only when schemas, SQLite migrations, compatibility fixtures, state transitions, authority rules, and fault cases are reviewable and executable under the standalone reference host. It does not start a background worker and does not change an integration gate.

## 2. Current facts that constrain the design

The current runtime already:

- derives a stable `operation_id` from host, principal, and idempotency key;
- persists accepted intent before acknowledgment;
- stores request/payload bytes, result/failure, state, certainty, runtime generation, and revision;
- writes an operation event for each lifecycle transition;
- rebinds `accepted` work and marks interrupted `running` work `indeterminate` on runtime restart;
- keeps event sequence globally increasing in SQLite;
- validates replay scope through session, lane, workspace, generation, revision, and parent operation;
- exposes status, cancel, and reconcile through MCP.

The current runtime does not yet represent execution attempts, leases, dispatcher ownership, checkpoint bindings, cursor pages, or a durable cancellation request distinct from terminal cancellation.

## 3. Contract decisions to freeze

### 3.1 Logical operation and execution attempt

`operation_id` remains the durable identity of one accepted intent. Recovery never replaces it.

Every actual execution uses an immutable attempt identity:

```text
OperationAttemptRef
  operation: OperationRef
  attempt_no: positive integer
  attempt_id: opaque deterministic-or-random identifier
  runtime_generation: positive integer
  dispatcher_generation: positive integer
```

A successor attempt increments `attempt_no`; the prior attempt remains visible. A late receipt from an older attempt cannot mutate the current logical operation.

### 3.2 Event cursor

Events remain ordered by one monotonic sequence per database, with filtering by operation. The public cursor is the last observed sequence, not a connection-local token.

```text
OperationEventPage
  operation
  after_sequence
  events[]
  next_sequence
  has_more
  terminal_snapshot?
```

Limits are explicit. The first candidate uses `default_limit=100`, `max_limit=500`, and an encoded response ceiling. A page may contain fewer rows than requested when the byte ceiling is reached.

### 3.3 Lease and generation fence

A lease authorizes one dispatcher generation to execute one attempt for a bounded interval. It contains:

- operation and attempt identity;
- runtime and dispatcher generation;
- monotonically increasing `lease_epoch`;
- owner digest, never a raw process command or credential;
- acquired, heartbeat, expiry, and optional release timestamps.

A state-changing receipt must match the current operation, attempt, runtime generation, dispatcher generation, and lease epoch. Expiry makes the old owner stale; expiry alone does not prove the attempt failed.

### 3.4 Recovery decision

Recovery emits a durable typed decision:

```text
resume_native
start_successor
restore_checkpoint
reconcile_effect
needs_user
terminal_from_receipt
quarantine
```

The decision binds its inputs, prior attempt, optional checkpoint/effect receipt, reason code, policy version, and resulting successor attempt. Unknown is never converted to failure or success.

### 3.5 Cancellation

A cancel request is durable control intent, not immediate proof that execution stopped. The v1 operation state remains `accepted` or `running` while `cancellation_requested=true`; terminal `cancelled` is written only after the current owner stops or recovery proves no owner can still commit.

Old v5 clients may continue to observe the existing operation state. The continuity profile exposes the additional control state.

### 3.6 Authority and reconnect

An operation ID is not a bearer credential. Read, cancel, reconcile, and event-page requests remain bound to host-provided principal/session authority and capability grants. A host may issue a continuity grant to a successor connection, but AAR core treats it as an opaque validated grant rather than a Hermes, AHC, or MCP session concept.

## 4. Proposed versioned models

Add an optional capability domain named `operation.continuity.v1` with:

- `OperationAttemptRefV1`;
- `OperationAttemptRecordV1`;
- `OperationLeaseRecordV1`;
- `OperationControlStateV1`;
- `OperationRecoveryPolicyV1`;
- `OperationRecoveryDecisionV1`;
- `OperationCheckpointBindingV1`;
- `OperationEventEnvelopeV1`;
- `OperationEventPageV1`;
- `OperationContinuitySnapshotV1`.

The profile is additive and negotiated. AAR v5 tools remain valid. The first new public tool is `aar_operation_events`; continuity fields may be added to a v6 status result only after exact compatibility fixtures exist.

Do not add AHC task IDs, channel identities, delivery state, Hermes conversation IDs, MCP connection IDs, provider credentials, or raw process invocation fields.

## 5. SQLite migration plan

The current database has no explicit migration registry. AR-LT0 first introduces:

```text
schema_migrations(version, applied_at_unix_ms, migration_digest)
```

Capture the current schema as baseline version 1 without rewriting existing operation rows. Apply later migrations under `BEGIN IMMEDIATE`, `PRAGMA foreign_keys=ON`, and `synchronous=FULL`.

New tables:

```text
operation_attempts
  operation_id, attempt_no, attempt_id
  runtime_generation, dispatcher_generation
  state, certainty, recovery_reason
  created_at, started_at, ended_at
  checkpoint_digest

operation_leases
  operation_id, attempt_no, lease_epoch
  owner_digest, acquired_at, heartbeat_at, expires_at, released_at

operation_controls
  operation_id, control_revision
  cancellation_requested, requested_at, requested_by_digest, reason_code

operation_checkpoints
  operation_id, attempt_no, checkpoint_digest
  checkpoint_kind, artifact_id, media_type, size_bytes
  created_by_operation_id, created_at

operation_recovery_decisions
  operation_id, decision_no, prior_attempt_no
  decision, reason_code, input_digest, checkpoint_digest
  successor_attempt_no, created_at
```

Extend `operation_events` additively with nullable `attempt_no`, `event_kind`, `payload_json`, and `payload_digest`. Existing rows are retained and classified as legacy lifecycle events during projection. Do not renumber historical sequences.

A pre-migration database copy and schema digest are mandatory in test evidence. Rollback uses the prior binary plus the pre-migration copy; no down migration rewrites a database that may already contain new attempts.

## 6. Source and fixture impact map

Expected implementation targets:

- `src/aar/schemas.py` — continuity model domain and version constants;
- `src/aar/runtime/models.py` — internal attempt/event/control projections;
- `src/aar/runtime/registry.py` — migrations, attempts, leases, pages, controls, recovery decisions;
- `src/aar/mcp/models.py` — bounded public continuity results;
- `src/aar/mcp/server.py` — negotiated events/status surface;
- `src/aar/runtime/reference_host.py` — capability declaration and reference authority policy;
- `tests/fixtures/` or a new versioned continuity fixture directory;
- `tests/test_contract.py`, `tests/test_reference_host.py`, `tests/test_mcp_server.py`, `tests/test_process_recovery.py`, `tests/test_ar23_regressions.py`;
- package/profile snapshots for Codex and Hermes compatibility.

No dispatcher or long-lived sidecar is added in this phase.

## 7. Ordered work packages

### LT0-A — failure taxonomy

Freeze transport loss, frontend exit, supervisor exit, dispatcher stall, worker death, lease expiry, stale generation, checkpoint incompatibility, broker uncertainty, cancellation race, and receipt-after-timeout cases.

### LT0-B — state machines

Publish valid transition tables for logical operations, attempts, leases, control requests, checkpoints, and recovery decisions. Include invalid and stale-generation transitions.

### LT0-C — schemas and canonical fixtures

Generate valid/invalid canonical JSON fixtures and exact digests. Test Python round-trip and strict rejection.

### LT0-D — migration registry

Add schema version discovery, atomic migration, old-database upgrade, interrupted-migration recovery, and unknown-newer-version refusal.

### LT0-E — event pages

Implement bounded `after_sequence` queries, limit/byte ceilings, zero-write read behavior, and terminal snapshot projection.

### LT0-F — authority fixtures

Prove operation ID alone cannot read or control another authority scope. Add successor-session grant fixtures without host-specific fields.

### LT0-G — compatibility profile

Keep the v5 surface unchanged; introduce the optional continuity capability and v6 candidate fixtures separately.

### LT0-H — AHC conformance mapping

Create fixture-only mappings to AHC's opaque durable task, lease/generation, recovery decision, backend cursor, and checkpoint references. No AHC package import is allowed.

### LT0-I — review and promotion evidence

Run reference-host fixtures, database migration tests, MCP schema checks, package/profile checks, and one independent bounded contract review.

## 8. Required fault fixtures

At minimum:

1. old database opens and migrates without changing existing operation/result bytes;
2. unknown newer schema fails closed;
3. duplicate submit returns the same logical operation;
4. same idempotency key with changed scope or payload conflicts;
5. attempt 1 expires, attempt 2 is created, attempt 1 late success is rejected;
6. cursor pages return each event once in stable order;
7. page limit and byte ceiling are enforced;
8. cancel request survives process restart;
9. unauthorized successor connection cannot read events;
10. authorized continuity grant can read without gaining mutation rights;
11. broker-started/no-receipt state yields `reconcile_effect` or `needs_user`, never blind replay;
12. checkpoint digest mismatch fails closed.

## 9. Exit criteria

AR-LT0 may be marked verified only when:

- all new models and transitions have valid/invalid fixtures;
- migration from the current schema and reopen with the new schema pass;
- event pagination is bounded, ordered, and read-only;
- stale attempt/generation/lease writes fail closed;
- reconnect authority is explicit and operation IDs grant nothing;
- v5 compatibility tests remain green;
- no background execution or restart-resume claim appears in public status;
- AHC coordination fixtures map semantics without importing AHC types;
- exact source, fixture, and evidence digests are recorded in the WAL.

## 10. Rollback and retained evidence

AR-LT0 rollout remains contract-only and must be reversible without guessing:

1. create a byte-for-byte database copy and record its digest before the first migration test;
2. run the additive migration only under an explicit schema-version check and one transaction;
3. keep the v5 projection writable throughout AR-LT0 so an old binary can continue against the
   pre-migration copy;
4. if migration/readback/compatibility fails, stop the candidate, retain the failed migrated copy and
   fixture outputs, and reopen the exact pre-migration copy with the prior executable;
5. do not implement a destructive down-migration or delete attempt/lease/recovery evidence;
6. revert capability advertisement and new fixture/schema bytes together if the contract is rejected.

A rollback proves restoration of the prior v5 behavior. It does not erase or reinterpret evidence
from the failed candidate.

## 11. Stop conditions

Stop and return to design if:

- the migration requires destructive rewrite of existing operation truth;
- a new enum value would silently break v5 clients;
- lease ownership depends on a transport connection;
- a task/session/delivery concept from AHC or Hermes is required in AAR core;
- cancellation is modeled as terminal before the worker is fenced;
- an indeterminate external effect would be retried automatically.

## 12. Handoff to AR-LT1

AR-LT1 starts only after AR-LT0 fixtures and migrations are verified. It consumes the frozen attempt, lease, event-page, control, and recovery models; it must not redesign them while implementing the dispatcher.

## 13. Verified implementation closeout

AR-LT0 is verified on the standalone candidate recorded in the append-only WAL. The implementation:

- freezes `aar.operation-continuity.v1` models and valid/invalid canonical fixtures;
- applies the additive SQLite v1-to-v2 migration and refuses unknown newer schemas;
- preserves the pre-migration database as the rollback authority rather than attempting a destructive
  down migration;
- implements immutable attempts, leases, full runtime/dispatcher/epoch/owner fencing, durable control
  state, recovery decisions, checkpoint bindings, and bounded event pages;
- exposes `aar_operation_events` through the v6 MCP surface while retaining v5-compatible operation
  state semantics;
- verifies zero-write reads, item and encoded-byte ceilings, stable cursors, stale-writer rejection,
  authority binding, exact-attempt checkpoint binding, and AHC-neutral fixture projection.

AR-LT0 does not claim a durable worker, supervisor survival, checkpoint restore, external-effect
exactly-once behavior, AHC authority, activation, or final delivery. Those boundaries remain owned by
AR-LT1 through AR-LT3 and the applicable host gates.
