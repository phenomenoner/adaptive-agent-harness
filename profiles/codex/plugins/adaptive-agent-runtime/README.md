# Codex host profile

Candidate package: `0.4.0a6`; bundled operation skill: `0.9.6`. The
source repository's machine-readable authority is `profiles/release-status-v1.json`; official
Plugin Directory publication is not completed.
Install the exact `adaptive-agent-runtime` wheel with `uv tool install --force <wheel>` so
`aar-codex-mcp` and its declared IPython, NumPy, and pandas dependencies are available, then run
`aar-codex-setup`. The setup command uses the marketplace bundled in the installed wheel and
preflights the declared plugin command and attached-supervisor mode.

The subprocess Codex route reports `NO_ATOMIC_AUTHORITY`: it reads current state and returns an
ordered manual/provider-authority-required plan before the first forward mutation. The host/operator
must apply that plan through the authoritative Codex configuration owner and rerun setup. There is
no automatic install, rollback, or best-effort compensation. A provider adapter may enable those
operations only after it supplies an opaque revision and expected-revision CAS contract.

Supervisor endpoint, credential, and shutdown-request paths are generation-unique. `discovery.json`
is a stable pointer that carries the publication ID and is advanced atomically. Normal lifecycle
cleanup is deliberately non-destructive and retains generation-specific control artifacts; do not
delete a successor by pathname or visible bytes. If native process identity is temporarily
unavailable, do not delete control files or start a second owner.

When setup reports `restart_required: true`, or a preserved manual receipt reports
`restart_required_after_manual_apply: true`, restart Codex Desktop, start a fresh task, load the
deferred capability tool when needed, and make one native `aar_capabilities` call. The old task's
catalog or a same-task transport error cannot prove that the candidate was picked up. Invoke
`$aar-operations` for public MCP workflows. Use `$aar-ipython-codegraph` only for explicitly
selected, digest-verified source artifacts and an already available external CodeGraph.
