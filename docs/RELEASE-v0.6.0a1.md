# Adaptive Agent Harness v0.6.0a1

Source snapshot: `phenomenoner/adaptive-agent-harness@v0.6.0a1`.

`v0.6.0a1` adds an opt-in standalone Hermes host adapter for the clean-install-only provider-ready
runtime introduced in `v0.6.0a0`. This immutable release snapshot is indexed by
[`profiles/release-status-v1.json`](../profiles/release-status-v1.json); exact post-freeze evidence is
bound by `adaptive-agent-runtime-v0.6.0a1-release-receipt.json`. It does not establish official Plugin
Directory publication, which requires separate external authority.

## Highlights

- `aar-hermes-mcp` starts or reuses one exact provider-ready supervisor, checks the current activation
  and route catalog, performs a live MCP capability readback, and then attaches stdio.
- `aar-hermes-authority issue` and `revoke` use the owner-only private supervisor channel for explicit,
  current-generation session-grant control.
- Grants remain memory-only. Install, startup, Ready, attach, reference-context, and ordinary mutation
  requests issue no grant; restart invalidates every prior grant. Every provider-ready mutation must
  instead present one current explicit session grant before its first durable write.
- `host-caller-driver-v1` binds real provider routes to the existing durable caller-work
  claim/mark-send/commit protocol; direct service-owned model sends fail closed.
- Private control frames are bounded by the existing process, generation, attachment-credential,
  deadline, capability, profile, route, policy, budget, principal, session, and TTL fences.
- The public 38-tool MCP v8 surface and frozen compatibility schemas remain unchanged.

See [Hermes provider-ready host adapter](HERMES-PROVIDER-READY.md) for configuration and operation.

## Compatibility

- Python `>=3.11,<3.15`
- MCP tool surface `aar.mcp-tools.v8` (unchanged)
- New optional private frame kinds: `grant_issue`, `grant_issued`, `grant_revoke`, and `grant_revoked`
- Existing Codex and generic MCP attach clients remain supported.
- `v0.6.0a0` artifacts and runtime roots remain immutable; deployment uses a new runtime root.

## Security and authority

The control CLI is a trusted-local host primitive. It authenticates with the owner-only attachment
credential and same-user private endpoint, but it does not create a broader remote authority model.
The supervisor clock owns issuance time, and the installed policy remains authoritative for allowed
principals, capabilities, budgets, and TTL.

A transport loss after the command is sent is reported as indeterminate rather than silently retried.
No attachment credential is printed or stored in a grant receipt.

AAR still does not own provider credentials, physical provider calls, external effects, activation
choice, or final delivery. IPython is not a security sandbox.

## Release boundary

This GitHub prerelease publishes source, tag, wheel, sdist, and release evidence after the named gates
complete. It does not claim PyPI publication, official Plugin Directory approval, provider-wide
execution, or general production deployment.
