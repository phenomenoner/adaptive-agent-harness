# Adaptive Agent Harness v0.4.0a6 candidate

This document describes the **unreleased** `0.4.0a6` source candidate. It is not a tag, GitHub
release, deployment, Plugin Directory submission, OpenAI approval, or provider-signed attestation.
The prepared immutable source reference is `phenomenoner/adaptive-agent-harness@v0.4.0a6`; it does
not exist until the release gate passes and that exact tag is published.
The machine-readable authority for this page and the other public release surfaces is
[`profiles/release-status-v1.json`](../profiles/release-status-v1.json).

## What changed in this candidate

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

The lifecycle repair matrix contains **63** required rows. The candidate status file currently
records the local repair result, Windows full suite, supported-Python CI, exact-wheel check, fresh
restarted host/Luna drill, and independent review as `PENDING`; no count or receipt is fabricated
here. Exact source commit, tree, and wheel fields remain null until a post-freeze external receipt
binds them.

The candidate is intended to be evaluated at T1 (the 63-row matrix), T2 (real process, publication,
provider, stop-wait, and client-attach seams), and T3 (supported hosts, exact wheel, restart/fresh
task, and the caller-delegated Luna/max drill). Passing a local unit subset alone is not a release
claim.

## Compatibility and publication boundary

The local developer `aar-mcp` surface remains host-neutral and does not receive provider
credentials, authorize effects, activate assets, or deliver messages. A provider-backed RLM call is
host-owned: the caller chooses one model and optional effort per job, and AAR records the resulting
ticket and receipt.

Official Plugin Directory deployment, reviewer access, OpenAI review/approval/publication, and
provider-signed attestation are **not completed** for this candidate. GitHub publication and local
Codex installation are separate later gates and must not be inferred from this document.

Historical `v0.4.0a5` material remains explicitly blocked/unreleased and must not be treated as the
current candidate.
