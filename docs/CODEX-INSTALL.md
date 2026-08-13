# Install AAR for Codex App

The normal installation path for the `v0.3.0a2` source prerelease is two commands. It does not run
the repository test matrix.
For an upgrade, close any Codex App task already using AAR before replacing the uv tool; Windows
otherwise keeps the old console entrypoint open until that MCP process exits.

From the pinned Git tag:

```powershell
uv tool install --force "git+https://github.com/phenomenoner/adaptive-agent-harness.git@v0.3.0a2"
aar-codex-setup
```

If a package-index build is published later, the equivalent first command is
`uv tool install --force adaptive-agent-runtime`. This prerelease does not promise package-index
publication.

Maintainers who already have an immutable wheel artifact may instead use:

```powershell
uv tool install --force D:\path\to\adaptive_agent_runtime-0.3.0a2-py3-none-any.whl
aar-codex-setup
```

`aar-codex-setup` performs four bounded actions:

1. runs a temporary real-MCP preflight that imports IPython, NumPy, and pandas in a supervised AAR
   worker, computes a three-row DataFrame result, inspects it, and closes the workspace;
2. adds or reuses the bundled `aar-local` marketplace;
3. installs or updates `adaptive-agent-runtime@aar-local` only when it is missing, disabled, or at
   another version;
4. keeps that plugin as the sole AAR MCP transport authority. If a legacy global `aar` server points
   to the same preflighted launcher, setup removes it; if it points elsewhere, setup stops without
   deleting the conflicting operator configuration.

The command prints a JSON receipt. A passing receipt must contain `status: "passed"`, dependency
versions, `tool_count: 30`, `workspace_closed: true`, the installed plugin version, and the exact
preflight launcher. `mcp_authority: "plugin"`, `legacy_global_mcp_removed`,
`configuration_changed`, and `plugin_changed` state what was selected or written. Restart the Codex
App only when `restart_required` is true, then call `aar_capabilities` in a fresh task. A no-op rerun
reports `restart_required: false`. Config, catalog, or setup output alone is not fresh-host runtime
proof.

Useful bounded variants:

```powershell
aar-codex-setup --preflight-only
aar-codex-setup --skip-preflight
aar-codex-setup --help
```

`--skip-preflight` is intended for a repeated configuration-only repair after the same installed
wheel has already passed. It is not the default.

Setup inspects the user config at `~/.codex/config.toml` by default. Use
`--codex-config <path>` only when the Codex host intentionally uses another config file. The legacy
entry is recognized from the actual `[mcp_servers.aar]` table, not from `codex mcp get`, because
that command can also display the correct plugin-provided server.

## Maintainer verification is separate

Release maintainers may additionally run Ruff, focused regressions, the full repository suite,
Python 3.11-3.14 compatibility, exact-wheel readback, and fresh Codex/Hermes scenarios. General
users do not need those development and release gates to install AAR.

The setup command does not grant provider credentials, external effects, activation, publication,
or final delivery. AAR computes and proposes; the host authorizes and delivers.
