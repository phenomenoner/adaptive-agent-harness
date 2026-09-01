# Adaptive Agent Harness v0.6.0a2

Target source reference: `phenomenoner/adaptive-agent-harness@v0.6.0a2`.

`v0.6.0a2` releases the provider-neutral caller-driver conformance guard merged after `v0.6.0a1`.
Its in-tree release contract is
[`profiles/release-status-v1.json`](../profiles/release-status-v1.json). This source does not
establish that the target tag, GitHub prerelease, or
`adaptive-agent-runtime-v0.6.0a2-release-receipt.json` exists; publication and exact post-freeze
evidence require external readback. It does not establish installed-runtime pickup, provider
qualification, formal evaluation, PyPI publication, or official Plugin Directory publication.

## Highlights

- Add the strict, versioned `aar.caller-driver-ready.v1` loopback-relay readiness envelope.
- Bind readiness to the operation, mutable ticket and `ticket_digest`, request, physical attempt,
  launch nonce, adapter identity, and adapter generation.
- Keep absolute path resolution, relay spawn, readiness parsing, schema/binding validation, and client
  construction before the durable may-have-sent boundary.
- Enforce the one-shot order `ready -> mark_send_started -> physical_send` with at most one physical
  send after a successful mark.
- Reject legacy `{ready, host, port}` readiness, binding drift, malformed mark receipts, mutable-ticket
  drift, and replay after send start.
- Preserve AAR's existing fail-closed, durable authority, no-resend, and `outcome_unknown` semantics.
- Keep the public 38-tool MCP v8 surface and frozen compatibility schemas unchanged.

## Compatibility

- Python `>=3.11,<3.15`
- MCP tool surface `aar.mcp-tools.v8` (unchanged)
- Provider-ready Hermes host adapter remains opt-in and requires its exact route catalog and default
  route profile.
- `v0.6.0a1` artifacts and release evidence remain immutable predecessors; consumers must install the
  successor artifact to obtain this guard.

## Security and authority

AAR still does not own provider credentials, physical provider calls, SDK retry policy, external
effects, activation choice, or final delivery. The host caller owns relay startup and the one physical
send, while AAR owns durable claim, cancellation, send-start, commit, and reconciliation authority.

Any local failure before `mark_send_started` leaves the ticket in the no-send region. After the
successful durable mark, an unavailable or ambiguous provider outcome is never silently retried or
reconstructed. Host-only evidence cannot rewrite durable authority behind the public API.

## Release boundary

If the named gates complete, the GitHub prerelease may publish source, tag, wheel, sdist, and release
evidence. This source file does not claim that publication occurred. Such a prerelease still would
not establish live Hermes pickup, provider route qualification, a paired formal score, PyPI
publication, official Plugin Directory approval, or general production deployment.
