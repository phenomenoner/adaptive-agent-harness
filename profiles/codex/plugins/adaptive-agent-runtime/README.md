# Codex host profile

Install the exact `adaptive-agent-runtime` wheel with `uv tool install --force <wheel>` so
`aar-mcp` and its declared IPython, NumPy, and pandas dependencies are available, then run
`aar-codex-setup`. The setup command uses the marketplace bundled in the installed wheel, installs
this plugin as the sole AAR MCP transport authority, and runs a minimal real-worker
dependency preflight. A matching legacy global AAR server is removed; a conflicting server
fails closed for operator review. Current plugin state is left untouched on a repeated setup; only
restart the Codex App when the receipt reports `restart_required: true`. Then start a fresh task and
invoke
`$aar-operations`. Use `$aar-ipython-codegraph` only for explicitly selected,
digest-verified source artifacts and an already available external CodeGraph.
The operation skill also covers bounded RLM jobs and immutable asset bundles; asset
import never activates or mutates host serving state.
