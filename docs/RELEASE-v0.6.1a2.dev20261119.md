# Adaptive Agent Harness v0.6.1a2.dev20261119

Target source reference: `phenomenoner/adaptive-agent-harness@v0.6.1a2.dev20261119`.

This alpha snapshot makes native workbench tasks easier to finish: the planner can
see committed work, select the artifacts it actually produced, and return a result
without losing the ownership or cleanup evidence needed by the host.

The in-tree release contract is
[`profiles/release-status-v1.json`](../profiles/release-status-v1.json).
This source does not establish that the target tag, GitHub prerelease, or
`adaptive-agent-runtime-v0.6.1a2.dev20261119-release-receipt.json` exists. This source file does not claim that publication occurred. Exact source, artifact, CI and publication identities
belong in the external release receipt rather than self-referential source assertions.

## Changes

- Retain a bounded summary of the first unresolved committed cell failure across
  inspection and format-correction turns. Only an identical successful directive in
  the same workspace generation clears that failure; no cell retry, capacity increase,
  or publication-rule relaxation is introduced.

- Give the planner bounded feedback from the last committed cell, including its source
  digest and the committed artifact-stage identifiers available for final delivery.
  Optional source feedback can be omitted when space is tight; mandatory task and
  authority context is never silently truncated.
- Carry a successful native completion proposal through the existing worker-result
  path. Commit and validate its named artifact selection before publishing the final
  result. Missing, duplicate, foreign or changed bindings remain rejected.
- Keep ordinary workspace result JSON intact. Helper-origin completion metadata is
  private in-memory state with a reserved native-persistence carrier, not a new public
  wire field or a grant of authority to arbitrary Python callers.
- Preserve certain planner and cell-budget failures separately from unresolved work.
  Terminal state and associated evidence are written through their owning transaction;
  an ambiguous effect is not made certain merely because a planner stopped.
- Close normal consumer IPC and terminal stdio resources through the selected ownership
  path. Bound owned-close waits even when an inherited writer keeps a buffered reader
  busy; pending or failed cleanup stays an explicit failure, not a closed-resource claim.
- Reject completion before artifact payloads are appended when its mandatory staging
  events cannot fit the remaining event capacity; optional progress cannot hide them.
- Retain fenced dispatch ownership, atomic artifact publication, committed usage joins,
  and digest-bound cancellation receipts. A stale owner cannot publish a successor's work.

## Compatibility and installation

The public MCP profile remains **`aar.mcp-tools.v9`**, with the same **38 tool names**.
Frozen v7/v8 contracts and the caller-owned
`claim -> mark-send-started -> physical send -> commit` flow remain unchanged.
Provider credentials, physical model calls, activation and final delivery stay host-owned.
No database migration, new service, public completion API or provider fallback is introduced.

Use Python 3.11–3.14 and an exact wheel. Installation remains **clean-install-only**:
choose a fresh absent runtime root and retain predecessor roots unchanged. Publication
does not update a live/default installation or authorize a production cutover.
Historical UNKNOWN outcomes and consumed attempt records are not reset or replayed.

## Verification and alpha qualification boundary

The release receipt records the exact source, wheel, source distribution, checksums,
install-candidate receipt, hosted CI result and asset readback. Source tests, package
identity, isolated startup and real-provider qualification remain separate evidence tiers.

All 90 runtime Python source files are byte-identical to the internal development
candidate used for two successful public native-task deliveries, including settled
accounting and verified required cleanup. This publication changes only release
documentation and bundled metadata; it is not a new target-wheel provider qualification. Any unavailable target-tag provider-ready
acceptance or independent formal/Claude CLI release review remains explicit `null` or
`NOT_RUN` in the external receipt; advisory findings are not substituted for those gates.

This source/package alpha does not establish repeated-output stability, hidden correctness,
a formal paired benchmark score, general production readiness, PyPI publication or official
Plugin Directory publication. Physical provider requests, retries and billing remain unknown
where they were not authoritatively observed.
