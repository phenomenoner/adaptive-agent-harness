# Receipt-backed model routing and fair evaluation

Adaptive Agent Harness (AAR) can route bounded model requests through a host-owned provider gateway without storing provider credentials or letting model-authored code choose an arbitrary endpoint. The same receipt boundary can support controlled comparisons between AAR workflows and another agent harness.

This document describes the public `0.3.0a2` contract. It is an integration boundary, not a bundled provider account or a benchmark result.

## Why the route belongs to the host

AAR owns durable operation state, request identity, budgets, route bindings, receipts, and reconciliation policy. The host owns:

- provider credentials and credential refresh;
- the allowlisted provider, model, and reasoning-effort policy;
- the physical SDK or gateway call;
- approval for billable inference;
- final acceptance and delivery.

AAR requests carry an owner-authored profile ID. They do not carry an API key, base URL, provider selector, or arbitrary model name.

```text
AAR operation
  -> immutable route-profile binding
  -> owner gateway / MCP Sampling back-channel
  -> physical provider request
  -> effective-route + usage receipt
  -> durable AAR model execution record
```

The provider gateway is optional. Without an owner-installed gateway and an authorized profile, provider-backed model requests remain unavailable.

## Route catalogs and admission

`ModelRouteProfile` declares a credential-free policy:

- profile ID;
- provider-driver ID;
- provider and model;
- reasoning effort;
- maximum output tokens;
- fallback policy;
- cache policy.

`ModelRouteCatalog` sorts profiles canonically and binds the complete catalog to a SHA-256 digest. At admission, `ModelRouteBinding` freezes both the catalog digest and the selected profile digest. A later catalog edit cannot silently alter an already accepted operation.

The broker rejects:

- unknown or duplicate profiles;
- profile or catalog digest mismatch;
- a gateway driver different from the bound driver;
- effective provider, model, or reasoning effort that differs from the binding;
- fallback when the profile says `none`;
- inconsistent usage totals;
- stale generation, grant, deadline, or idempotency context.

## Host gateways

A provider integration implements the small `OwnerGatewayTransport` boundary. The generic broker issues a deterministic provider request ID and supplies the admitted route binding, deadline, and ephemeral owner authority. The gateway returns a `GatewayModelResult` with:

- provider request ID;
- output text and finish reason;
- effective provider, model, and reasoning effort;
- provider-reported input, output, cache, and reasoning-token fields when available;
- total tokens;
- retry ordinal.

Credential resolution occurs immediately before the host call. Credential bytes are neither part of the route catalog nor persisted in AAR requests, journals, traces, or benchmark fragments.

## MCP Sampling with Hermes or another capable client

`McpSamplingGatewayTransport` maps an AAR model request onto the owning MCP session's Sampling back-channel. The client remains the physical provider-call owner.

The client response must include `aar.model-receipt.v1` metadata that matches:

- the deterministic provider request ID;
- the bound provider, model, and reasoning effort;
- the model reported by the MCP Sampling result;
- provider-reported token totals;
- retry count;
- fallback chain.

A stdio or another genuinely bidirectional MCP transport is required. A client without a Sampling back-channel fails closed.

MCP Sampling does not currently provide a portable provider-receipt lookup API. If a request may have been sent but the response is lost, the operation becomes `indeterminate` and is quarantined rather than blindly replayed. A pre-send expiry is a certain failure; a post-send timeout, cancellation race, disconnect, or ownership loss is an unknown outcome until an authoritative host receipt can reconcile it.

## Fair `gpt-5.6-luna` / `max` comparisons

AAR includes a narrow evidence adapter and JSON Schemas for paired AAR-versus-Prime-style evaluations. They define evidence shape; they do not run an evaluation or declare a winner.

The paired route is fixed to:

```text
provider:         openai-codex
model:            gpt-5.6-luna
reasoning effort: max
fallback:         none
```

For a fair comparison, both arms must additionally share:

1. the same immutable fixture and oracle bytes;
2. the same task prompt and scoring rules;
3. the same wall-time and provider-token budgets;
4. the same tool and external-effect policy, or a declared native-track difference;
5. the same cache policy and run-order/randomization policy;
6. provider-reported usage rows for every attempt, including coordination, verification, retry, and recovery calls;
7. requested **and effective** route evidence;
8. the same timeout, cancellation, and indeterminate-outcome classification;
9. immutable contender version and launch digests;
10. no hidden fallback or unaccounted internal retry.

`AarPrimeBenchmarkEvidenceAdapter` emits only bounded route and usage fragments. It refuses evidence when:

- requested or effective route is not exactly `openai-codex / gpt-5.6-luna / max`;
- fallback occurred;
- usage is not provider-reported;
- an internal retry exists without authoritative per-attempt accounting;
- the call is marked wasted.

The bundled schemas are:

- `aar.integrations/contracts/run-manifest.schema.json`;
- `aar.integrations/contracts/usage-ledger-row.schema.json`.

A valid manifest records the contender, fixture, oracle, route-policy digest, budgets, attempt, status, and digest references to artifacts and usage. A run that cannot prove those fields should be marked `inadmissible`, not silently included in aggregate results.

## Credential-free preflight

The public test suite exercises the complete contract without calling a provider:

```bash
uv run pytest -q \
  tests/test_model_broker_registry.py \
  tests/test_model_benchmark_adapter.py \
  tests/test_mcp_sampling_gateway.py \
  tests/test_mcp_sampling_integration.py
```

This verifies route binding, durable lifecycle, receipt parsing, usage projection, strict no-fallback behavior, timeout/cancellation classification, and a bidirectional synthetic MCP Sampling path. It does **not** prove that a particular host account is authorized or that a live provider route is available.

## Live qualification versus formal evaluation

Treat these as separate actions:

- **Credential-free preflight:** validates code, schemas, and no-network transport behavior.
- **Owner-authorized qualification:** sends a minimal provider request to prove one exact installed host route and receipt path.
- **Formal evaluation:** runs frozen cases under a declared protocol and produces scoreable artifacts and complete usage ledgers.

Installing AAR does not authorize the latter two. The operator should make billable qualification and formal evaluation explicit, bounded decisions.

## Security and operational boundaries

- AAR is not a security sandbox for model-authored Python.
- Provider credentials remain host-owned.
- Route metadata is policy, not authentication.
- MCP client metadata is not trusted as authorization by itself.
- Unknown provider outcomes are not rewritten as success or failure.
- No generic exactly-once provider-call guarantee is claimed.
- The benchmark adapter verifies evidence eligibility; it does not ensure scientific validity outside the frozen protocol.
