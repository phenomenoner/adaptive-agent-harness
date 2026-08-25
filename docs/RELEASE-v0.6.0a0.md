# Adaptive Agent Harness v0.6.0a0 release notes

This document describes the **`0.6.0a0` public GitHub alpha release** at
`phenomenoner/adaptive-agent-harness@v0.6.0a0`. It does not claim package-index publication,
production deployment, provider execution, or official Plugin Directory publication.

## What changed in this release

- Provider-ready installation is clean-install-only. Existing targets, symlinks, unknown residual
  state, and unsafe path identities fail closed without adoption or replacement.
- A fresh installation creates canonical v6 registry, profile, history/current, installation
  evidence, initial activation authority, route/grant policy, and package-owned broker mappings.
- Runtime construction binds the actual `TypedBrokerFacade` before provider-ready capability
  projection, activation, durable dispatch, MCP admission, or Ready publication.
- Session grants remain memory-only and explicitly issued. Missing, stale, revoked, expired,
  wrong-session, wrong-capability, and mixed grant sets are denied before backend availability.
- The existing MCP v8 tool names and frozen compatibility schema bytes remain unchanged. Runtime
  handshake and capability metadata report the `0.6.0a0` package version.

## Verification boundary

The release candidate is gated by exact generated assets, strict contract validation, the revised
201-row T0–T3 no-live acceptance matrix, an exact wheel, a source-isolated fresh installed-host
lifecycle, durable RLM/IPython functional evidence, one authorized Hermes provider smoke, and an
independent exact-byte review. Release receipts and final test totals are published only after those
gates pass on the final candidate bytes.

Historical `v0.5.0a0` remains an immutable prior release. This release does not rewrite its frozen
MCP v8 compatibility baseline or historical release evidence.