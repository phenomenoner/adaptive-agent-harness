# Adaptive Agent Harness v0.4.0a5 — exact lifecycle and installer fencing alpha

`v0.4.0a5` hardens the local Codex lifecycle introduced in `v0.4.0a4`. It keeps the caller-
delegated public RLM product from `v0.4.0a0`: the host still performs every physical model call,
owns provider credentials, and selects one model and optional effort per job.

## What changed

- Process ownership is now observed as `MATCH`, `MISMATCH`, or `UNAVAILABLE`. Temporary native
  observation failure never becomes permission to delete control state, signal a PID, finalize a
  worker, or admit a conflicting successor.
- Linux fallback signalling uses a verified pidfd; Windows termination and terminal readback use
  one verified process handle. PID reuse between observation and signal cannot redirect the action.
- Supervisor startup reaps its exact child before releasing startup ownership on every timeout or
  post-spawn failure. A foreign Ready owner is accepted only after the spawned contender is
  terminal.
- Unix supervisor endpoints include the process identity. Endpoint, request, credential, and
  discovery cleanup keeps discovery as the final publication fence and preserves replacement-owned
  state.
- Managed checkpoint restore stages an unregistered worker, restores the checkpoint, then performs
  a terminal old-binding handoff before registering and publishing the successor.
- Windows virtual-environment launches bypass the short-lived launcher redirector while retaining
  the active environment's site packages and `sys.executable` identity.
- Codex marketplace, plugin, and legacy global-MCP configuration is one readback-driven
  transaction. A command failure, unexpected plugin state, unreadable state, or concurrent drift is
  contained unless the current state is proven transaction-owned.
- A production startup failure after final configuration commit reports
  `configuration_committed_runtime_unready`; it is retry-safe retained configuration, not success
  and not rollback.
- The canonical `aar-operations` skill is `0.9.5` and carries the same recovery, restart, fresh-task,
  and native-call boundaries into the Codex and Hermes bundles.

## Install or upgrade

Stop the exact Codex-owned runtime before replacing a live Windows tool environment:

```powershell
aar-codex-setup --stop-runtime
uv tool install --force "git+https://github.com/phenomenoner/adaptive-agent-harness.git@v0.4.0a5"
aar-codex-setup
```

When setup reports `restart_required: true`, restart Codex Desktop, create a fresh task, and make
one native `aar_capabilities` call. Loading a deferred tool, reading config, or seeing a catalog row
does not prove that the new MCP process is active.

## Verification contract

- focused identity, exact-handle, launcher, cleanup, worker, transaction, and real-child tests;
- 445 passing Windows repository tests, one directory-symlink capability skip, and the expected
  warning from the deprecated MCP Sampling compatibility path;
- supervisor/frontend reconnect, hard-owner-loss, durable RLM, and managed checkpoint-restore
  scenarios;
- generated contract, MCP surface, operation-skill, Codex profile, and Hermes profile parity;
- an isolated exact wheel with RECORD verification and declared-command setup preflight;
- the full Python 3.11–3.14 Linux GitHub Actions matrix on the exact commit before tag creation;
- an exact-wheel local install followed by a restarted Codex Desktop and fresh-task native call;
- a fresh independent release review bound to the final commit and acceptance artifacts.

## Boundaries

- AAR is programmable execution, not a security sandbox.
- GitHub publication and verified local Codex use are not production deployment, ChatGPT
  execution, OpenAI approval, or official Plugin Directory publication.
- The public remote RLM service never receives provider credentials and never performs the host's
  model call.
- Process or configuration uncertainty is retained for recovery; it is never silently converted
  into absence, rollback, or success.

See [Codex installation](CODEX-INSTALL.md), [Host compatibility](../HOST-COMPATIBILITY.md), and the
[changelog](../CHANGELOG.md) for operational details.
