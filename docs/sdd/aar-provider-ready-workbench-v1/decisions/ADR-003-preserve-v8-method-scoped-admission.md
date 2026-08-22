# ADR-003 — Preserve MCP v8; Use Method-Scoped Admission

**Status:** proposed

**Date:** 2026-08-21

## Context

The v8 surface is frozen and additive over v7. The product gap can be closed without changing job/tool schemas, but requiring all six broker methods for every job forces false adapters or unnecessary product scope.

## Decision

Preserve all v7/v8 tool names, order, input/output schemas, and capability compatibility. Each activation profile selects exactly one planner mode: normalized job `caller_delegated→caller_delegated_ticketed` requires its exact caller-driver `model.request` manifest/factory and `start_only`, while `service_managed→service_managed` requires its exact native `model.request` manifest/factory from the same immutable registry. Resolve the frozen sorted-unique `grant_ids` array into one coherent current server-side set, reject mixed/duplicate/missing/revoked/expired authority, then derive the remaining required methods as a pure pre-operation function of normalized mode, budgets/features, effective capability union, and matching package factories. Missing entitlement or retired/stale factory authority is `GRANT_DENIED`; only valid current grants plus same-generation instantiated-adapter health loss is `CAPABILITY_UNAVAILABLE`. Omit unavailable optional capabilities and fail their invocation before send.

## Consequences

- No v9 MCP surface or registry v7 is required for the first provider-ready release because strict planner owner/request fields and the cell-free outbox CAS map to existing v6 columns and constraints.
- The planner branch must use the unique waiting-external operation event, operation attempt/lease/dispatch and outbox tuple; dead prepared owners are fenced by a generation-advancing prepared takeover, and consumption plus directive projection is atomic. It never synthesizes a cell or uses cell-bound attempt-authority/rebind rows. If implementation cannot enforce this exact mapping without sidecar authority or DDL change, registry v7 and a superseding ADR are mandatory.
- A model-only Luna/max workbench profile can be truthful and usable.
- Artifact/subagent-enabled jobs still fail early when their adapters are absent.
- Evidence/effect calls remain capability-gated at invocation unless an effective grant makes them admission-required.
- A future explicit job method allowlist may justify a reviewed v9 successor.

## Rejected

- Mutating frozen v8 schemas in place.
- Adding versioned duplicate tools before necessity is proven.
- Declaring every adapter configured to satisfy a global check.
- Allowing optional calls to reach a physical driver before capability failure.
