# ADR-002 — Ticket Caller-Delegated Root Planner Calls

**Status:** proposed

**Date:** 2026-08-21

## Context

The v0.5 coordinator requires an injected synchronous `RlmWorkbenchPlanner`. Cell broker calls can suspend through caller-work tickets, but initial/correction/recovery/finalizer model calls lack a product caller-delegated path. Merely configuring method rows leaves runtime execution blocked at `workbench planner is not configured`.

## Decision

For `execution_mode=caller_delegated`, persist every root planner call as existing `model.request` caller work. Strict owner/request schemas map to v6 suspension, ticket, candidate/command receipt and successor-outbox fields. Because a root planner is not a cell, its suspension has SQL-null `cell_execution_id`; the unique predecessor is selected by the existing waiting-external `operation_events` row and `attempt_no`, never by latest-attempt inference. Planner prepare/consume is a cell-free CAS over the released predecessor lease and outbox: `pending→prepared`, or a generation-advancing `prepared→prepared` takeover only after the stored successor lease and dispatch authority are both dead. The exact current successor commits `prepared→consumed`, exactly one valid-directive/correction/certain-failure/cancel/deadline projection, and its event in one registry transaction; a possibly-sent outcome-unknown never prepares or consumes and remains reconciler-owned. Planner flow never reads/writes cell-bound `rlm_workbench_attempt_authority` or `rlm_workbench_rebind_transfers`; those remain cell-only. Release local ownership while waiting, accept only reconciler-settled route/usage/directive evidence, and fence any stale prepared owner. Fail-if-called synchronous planner and zero-cell-table-access canaries plus crash-before/after atomic-consume evidence are mandatory.

The activation intent's strict mode-tagged planner object also supports one service-managed profile. That mode must select the exact native `model.request` manifest/factory from the same package-owned immutable method registry; admission normalizes `caller_delegated` to `caller_delegated_ticketed` and maps `service_managed` to itself before comparison. There is no second native-planner constructor namespace.

## Consequences

- Root and cell model calls share send-start, attempt, receipt, cancellation, timeout, and reconciliation semantics.
- AAR itself causes and owns each benchmark-relevant ticket.
- No live Python stack is serialized while waiting.
- Corrections/recovery remain visible and charged.
- Coordinator complexity grows, but the cell-free outbox branch remains inside the existing v6 attempt/lease/suspension/ticket lifecycle and adds no table or schema version.

## Rejected

- Blocking the caller until it services work that has not been returned.
- Holding an attempt lease across indefinite external I/O.
- Precomputing planner directives outside AAR and calling them AAR treatment.
- Blind retry after an indeterminate provider send.
