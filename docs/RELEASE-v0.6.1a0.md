# Adaptive Agent Harness v0.6.1a0

Target source reference: `phenomenoner/adaptive-agent-harness@v0.6.1a0`.

This release contract targets more reliable long-running work: renew the existing owner lease,
respect the caller's actual model and correction allowances, and keep declared workspace limits
consistent with the executable backend.

The in-tree release contract is
[`profiles/release-status-v1.json`](../profiles/release-status-v1.json).
This source does not establish that the target tag, GitHub prerelease, or
`adaptive-agent-runtime-v0.6.1a0-release-receipt.json` exists.
This source file does not claim that publication occurred. Updating a source branch or installing a
local wheel is distinct from
publishing a target tag, qualifying a provider, or completing a formal evaluation. Plugin Directory
publication, PyPI publication, provider-signed attestation, and general production deployment
require separate authority and evidence.

## Changes

- Renew the existing fenced dispatch lease during active local workbench execution. Stop renewing
  when that attempt suspends or returns; an expired or replaced owner still cannot commit.
- Check the root planner's model-call allowance before creating its next ticket or requesting its
  next service-managed model call. Invalid-directive corrections respect `max_corrections`, including
  zero, as well as the total model-call allowance.
- Let the host configure finite workbench, cell, activation and grant time allowances instead of
  enforcing a universal 15-minute job or one-minute cell maximum. Installed grants, actual deadlines,
  strict integer validation, signed-64 serialization and effect ownership remain binding.
- Accept the advertised workbench event range of 1–512 and output range of 1–262,144 characters in
  the shared backend. Retention/truncation remains bounded by the requested values.
- Retain the prior runtime's evaluated artifact-binding and worker-result fixes so published output
  keeps its creating-operation and manifest identity across the actual runtime boundary.

## Contract compatibility

The current descriptor profile is **`aar.mcp-tools.v9`**, with the same 38 tool names and the same
caller-owned `claim -> mark-send-started -> physical send -> commit` flow. It does not introduce a
new MCP method or transfer provider credentials, physical calls, activation or delivery authority
to AAR.

The canonical generator and runtime validator share one pure constraint compiler over the frozen
contract definitions. The v9 tool descriptor publishes the resulting constraints. Frozen v7/v8
manifests, workbench-v1 and caller-work-v1 source bytes remain unchanged; old identities, token
bounds and byte bounds are not rewritten by a blanket numeric replacement. Capability readers can
validate retained v8 evidence against its retained v8 descriptor rather than relabeling it as v9.
The frozen provider-ready v1 schema bundle and its 64-fixture manifest also remain byte-identical;
`schemas/aar-provider-ready-schemas-v2.json` records the current executable owner-time constraints.
It changes no activation message identity or authority flow.
`aar_capabilities` keeps the 30-tool v7 compatibility projection; use
`aar_rlm_workbench_capabilities` and real tool discovery for the current workbench profile.

## Installation and host behavior

Use Python 3.11–3.14 and an exact wheel. This is still **clean-install-only**: choose a fresh absent
runtime root and retain predecessor roots unchanged. Installation does not start a model call,
create an operating-system service, or issue a session grant. The host/operator owns its integration
binding and must separately verify native pickup after changing it.

The optional Hermes host adapter and explicit current-generation authority CLI remain supported.
Existing runtime roots, historical UNKNOWN outcomes, accounting rows, and physical-send receipts
are not migrated or silently reset. An uncertain provider effect must never be replayed merely
because a new runtime was installed.

## Verification boundary

Generated-byte parity, strict model/technical boundaries, fenced lease and planner/correction
regressions, real SDK transport/list/call behavior, package assets and the repository's supported
Python checks are relevant evidence for this change. Exact post-freeze results belong in an
external receipt, not in self-referential source claims. A no-provider probe cannot establish a
provider-backed task, route qualification, comparative score, or resolution of an old transport
incident. A historical HTTP 200 response with an unknown binary Content-Type remains unexplained
without its missing original headers/body; this release does not invent that root cause.
