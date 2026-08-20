# Adaptive Agent Harness v0.5.0a0 release notes

This document describes the **`0.5.0a0` public GitHub alpha release** at
`phenomenoner/adaptive-agent-harness@v0.5.0a0`. It does not claim package-index publication,
production deployment, provider execution, or official Plugin Directory publication.

## What changed in this release

- The executable MCP surface advances additively from 30 to 38 tools.
- Three RLM-native workbench tools provide capability discovery, bounded admission, and durable status.
- Five caller-work lifecycle tools expose claim, send-start, certain pre-send cancellation, receipt
  commit, and claimant-bound reconciliation.
- Frozen v7 `aar_capabilities` bytes remain a 30-tool compatibility projection; executable discovery
  is the exact v7 prefix followed by the reviewed eight v8 additions.
- Workbench capability truth is owned by the configured host. Unconfigured methods remain unavailable,
  and admission fails closed rather than implying provider or backend support.
- MCP caller-work tools forward to the existing durable runtime authority, preserving deadline,
  idempotency, physical-attempt, receipt, reconciliation, stale-writer, and terminal-state fences.

## Verification boundary

The exact candidate passed generated-contract, schema, profile, skill, package, exact-wheel,
fresh-host, full-suite, and independent current-byte review gates. The final source suite reported
**743 passed, 5 platform-gated skips, and 1 existing MCP Sampling deprecation warning**. The installed
wheel exposes `aar.mcp-tools.v8`, 38 executable tools, the exact eight-tool successor suffix, and the
frozen 30-tool v7 compatibility projection.

Historical `v0.4.0a6` remains an immutable prior release snapshot governed by
`profiles/release-status-v1.json` and its external receipt. This release does not rewrite that
historical evidence or the frozen v7 compatibility baseline.
