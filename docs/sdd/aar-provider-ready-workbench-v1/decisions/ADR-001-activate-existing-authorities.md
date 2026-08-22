# ADR-001 — Activate Existing Authorities; Do Not Add Another Broker

**Status:** proposed

**Date:** 2026-08-21

## Context

AAR v0.5 already contains AR-MB route bindings, owner-gateway evidence contracts, model receipts/usage journals, registry-v6 workbench/caller-work tables, a durable supervisor, and MCP v8 tools. The installed default host nevertheless reports unconfigured workbench backends and publishes no execute grant.

## Decision

Build the successor around an operator-owned activation profile and one truthful v5→v6 cutover transaction that composes existing primitives. An empty runtime first uses the existing Registry transaction to create canonical empty v5, then enters the same snapshot/row-set-bound cutover; no separate direct-v6 receipt or attestation is introduced. Do not add a new broker service, provider client, credential store, daemon, state database, or MCP inference back-channel.

## Consequences

- Credentials remain host-owned.
- AAR owns durable operation/ticket/reconciliation state.
- Capability truth is derived from instantiated adapters and exact manifests.
- Installation alone remains fail-closed.
- Operator tooling and profile binding become first-class product contracts.
- A future host driver can vary without changing core provider credentials or workbench lifecycle.

## Rejected

- Marking rows configured from profile booleans.
- Embedding OpenAI Codex credentials/SDK behavior in AAR core.
- Treating deprecated MCP Sampling as the production route.
- Evaluator monkey-patching or transport interception.
