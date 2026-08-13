# Adaptive Agent Harness v0.3.0a2 — Receipt-Backed Model Routing Alpha

**Bind the route, keep credentials in the host, and retain evidence for what actually ran.**

This public-alpha release adds a durable production-model boundary to Adaptive Agent Harness (AAR). A host can authorize one credential-free route profile, perform the physical provider call, and return an attested route and usage receipt. AAR preserves durable lifecycle and uncertainty without becoming a provider account, credential store, or final-delivery system.

## What changed

### Owner-controlled routes

- Route catalogs are canonical, digest-bound, and owner-authored.
- Admission freezes the exact provider driver, provider, model, reasoning effort, output-token limit, fallback policy, and cache policy.
- Model-authored code cannot supply a credential, endpoint, provider selector, or arbitrary model name.

### Durable model execution

- Model requests receive deterministic provider-request identities.
- The journal distinguishes pre-send admission, physical-send uncertainty, route receipt, provider usage, result commit, failure, indeterminate outcome, and quarantine.
- Pre-send expiry prevents the provider call.
- A post-send timeout, cancellation race, disconnect, or ownership loss remains unknown instead of being blindly replayed.

### Host gateway and MCP Sampling

- `OwnerGatewayTransport` is the small provider-driver boundary.
- `McpSamplingGatewayTransport` uses the owning MCP session's bidirectional Sampling back-channel.
- The host owns credentials and the physical call.
- A successful response must carry `aar.model-receipt.v1` metadata with matching request identity, effective route, provider-reported usage, retry count, and fallback chain.
- Route drift, forbidden fallback, inconsistent totals, and missing receipt evidence fail closed.

### Controlled evaluation evidence

The release includes a narrow AAR-versus-Prime-style evidence adapter and pinned JSON Schemas for:

- immutable run manifests;
- requested and effective route proof;
- per-request provider usage rows;
- fixture, oracle, launch, tool-policy, artifact, and usage-ledger digests;
- explicit `inadmissible` status when required evidence is absent.

The paired route is fixed to:

```text
openai-codex / gpt-5.6-luna / max
```

The adapter rejects fallback, non-provider usage, route drift, wasted calls, and internal retry without authoritative attempt accounting. These are evidence-eligibility rules, not a benchmark score or winner.

### Updated operation guidance

The bundled `aar-operations` skill is now `0.9.0`. It teaches agents to:

- use only host-owned model routes;
- validate route and usage receipts;
- preserve `indeterminate` outcomes;
- reconcile instead of blindly replaying;
- distinguish credential-free preflight, owner-authorized qualification, and formal evaluation;
- mark an evaluation run inadmissible when route or usage evidence cannot be proven.

The canonical skill bytes are copied into both Codex and Hermes bundles and bound by package metadata and host-profile digests.

## Install

```bash
uv tool install --force \
  "git+https://github.com/phenomenoner/adaptive-agent-harness.git@v0.3.0a2"
```

For Codex App:

```bash
aar-codex-setup
```

For Hermes, register the installed `aar-mcp` command through the bundled profile or your existing MCP configuration, then verify it from a fresh client process. Installing the package does not configure a provider account or authorize a billable request.

## Verify from source

```bash
git clone --branch v0.3.0a2 \
  https://github.com/phenomenoner/adaptive-agent-harness.git
cd adaptive-agent-harness
uv sync --locked
uv run aar-contract verify
uv run aar-mcp-assets verify
uv run aar-host-assets verify
uv run pytest -q
uv run ruff check .
python3 tools/public_release_check.py
```

The release source suite passes **326 tests** with one platform-gated Windows process-identity skip.
The only warning is the upstream SEP-2577 deprecation of MCP Sampling in protocol `2026-07-28`;
clients using the supported Sampling-compatible protocol path remain covered by the integration
tests and must still return the exact receipt described above.

A credential-free focused model-routing preflight is documented in [Receipt-backed model routing and fair evaluation](https://github.com/phenomenoner/adaptive-agent-harness/blob/v0.3.0a2/docs/MODEL-ROUTING-AND-EVALUATION.md).

## Important alpha boundaries

- AAR is not a security sandbox for model-authored Python.
- Provider credentials remain host-owned.
- The bundled profile does not grant provider access or authorize inference.
- MCP Sampling has no portable provider-receipt lookup API; unresolved post-send outcomes remain indeterminate or quarantined.
- No generic exactly-once provider-call guarantee is claimed.
- The evaluation adapter validates evidence shape and eligibility; it does not establish scientific validity outside the frozen protocol.
- This release contains no benchmark result, score, ranking, or winner.
- Managed multi-tenant isolation, generic external-effect execution, activation, and final delivery remain outside AAR authority.
- Package-index publication is not promised; install from the pinned Git tag above.

This GitHub release includes the wheel and its matching `.sha256` sidecar in addition to GitHub-generated source archives.

## Learn more

- [README](https://github.com/phenomenoner/adaptive-agent-harness#readme)
- [Model routing and fair evaluation](https://github.com/phenomenoner/adaptive-agent-harness/blob/v0.3.0a2/docs/MODEL-ROUTING-AND-EVALUATION.md)
- [Architecture](https://github.com/phenomenoner/adaptive-agent-harness/blob/v0.3.0a2/ARCHITECTURE.md)
- [Technical status](https://github.com/phenomenoner/adaptive-agent-harness/blob/v0.3.0a2/TECHNICAL-STATUS.md)
- [Security](https://github.com/phenomenoner/adaptive-agent-harness/blob/v0.3.0a2/SECURITY.md)
- [Changelog](https://github.com/phenomenoner/adaptive-agent-harness/blob/v0.3.0a2/CHANGELOG.md)

#RLM #IPython #AIAgents #MCP #AgentInfrastructure #Python
