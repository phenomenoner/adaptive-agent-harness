# Adaptive Agent Harness v0.4.0a6 release notes

This document is the **`0.4.0a6` release snapshot**. Its stable source reference is
`phenomenoner/adaptive-agent-harness@v0.4.0a6`. The immutable in-tree contract is
[`profiles/release-status-v1.json`](../profiles/release-status-v1.json); exact source, wheel, CI,
install, fresh-host/RLM, review, tag, and downloaded-asset evidence is bound separately by
`adaptive-agent-runtime-v0.4.0a6-release-receipt.json`.

## What changed in this release

- Spawned supervisor and IPython children are admitted with one exact native process object and
  terminalized through that object, including same-handle wait and at-most-once terminal receipts.
- Each continuity database admits one active runtime through the existing process lock acquired
  before host construction. Losing contenders fail closed, and process exit releases ownership without a
  durable reservation or lease-recovery protocol.
- Supervisor endpoint, credential, and shutdown-request paths are generation-unique;
  `discovery.json` is a stable pointer carrying the publication ID and advances atomically. Normal
  lifecycle cleanup and shutdown-request consumption are **non-destructive**; retained
  generation-specific control artifacts are forensic state and are not permission to remove a
  successor.
- The subprocess Codex configuration adapter reports `NO_ATOMIC_AUTHORITY`. It reads current state
  and returns an ordered manual/provider-authority-required plan before the first forward mutation.
  It does not perform automatic installation, rollback, or best-effort compensation. A non-empty
  plan receipt sets `restart_required_after_manual_apply: true`, so a later no-op inspection cannot
  obscure the required Codex restart.
- The canonical `aar-operations` skill is `0.9.6` and documents the provider CAS boundary, manual
  setup plan, retained generations, and the required fresh-task restart verification.

## Verification state

The lifecycle repair matrix contains **63** required rows, and its Windows behavioral evidence is
completed pre-freeze. Exact source commit/tree, supported-Python CI, wheel and sidecar, local
install, fresh native pickup, caller-delegated Luna/max drill, independent review, tag target, and
downloaded-asset readback are recorded in the external release receipt rather than inside the Git
tree or wheel they hash.

Verification uses T1 for the 63-row matrix, T2 for real process, publication, provider, stop-wait,
and client-attach seams, and T3 for supported hosts, the exact wheel, restart/fresh task, and the
caller-delegated Luna/max drill. Passing a local unit subset alone does not establish the external
receipt claim.

## Compatibility and publication boundary

The local developer `aar-mcp` surface remains host-neutral and does not receive provider
credentials, authorize effects, activate assets, or deliver messages. A provider-backed RLM call is
host-owned: the caller chooses one model and optional effort per job, and AAR records the resulting
ticket and receipt.

This GitHub release snapshot does not establish official Plugin Directory deployment, reviewer
access, OpenAI review/approval/publication, or provider-signed attestation. Those require separate
external authority and must not be inferred from this document.

Historical `v0.4.0a5` material remains explicitly blocked/unreleased and must not be treated as the
current release.
