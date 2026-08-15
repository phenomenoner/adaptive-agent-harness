# Install AAR for Codex App

The normal installation path is two commands. It does not run the repository test matrix.
For an upgrade from `0.4.0a4` or later, stop the exact Codex-owned runtime first, then close any
Codex App task already using AAR before replacing the uv tool. On Windows, either the frontend or
its detached supervisor can otherwise keep the old entrypoint open:

```powershell
aar-codex-setup --stop-runtime
```

From the pinned public tag:

```powershell
uv tool install --force "git+https://github.com/phenomenoner/adaptive-agent-harness.git@v0.4.0a5"
aar-codex-setup
```

If a package-index build is published later, the equivalent first command is
`uv tool install --force adaptive-agent-runtime`. This prerelease does not promise package-index
publication.

Maintainers who already have an immutable wheel may instead use:

```powershell
uv tool install --force D:\path\to\adaptive_agent_runtime-0.4.0a5-py3-none-any.whl
aar-codex-setup
```

`aar-codex-setup` performs seven bounded actions:

1. reads the bundled plugin's exact MCP command and arguments (`aar-codex-mcp`, empty arguments);
2. runs that exact declaration in an isolated Codex runtime, verifies an attached supervisor,
   imports IPython, NumPy, and pandas in a supervised worker, computes and inspects a three-row
   DataFrame result, closes the workspace, and stops the isolated supervisor;
3. adds the bundled `aar-local` marketplace or replaces a same-name entry that points to another
   root. Marketplace name and plugin version equality alone are not accepted as byte identity;
4. installs or updates `adaptive-agent-runtime@aar-local` when it is missing, disabled, at another
   version, or sourced from the replaced root;
5. keeps that plugin as the sole AAR MCP transport authority. If a legacy global `aar` server points
   to the same package launcher, setup removes it; if it points elsewhere, setup stops without
   deleting the conflicting operator configuration;
6. verifies each configuration mutation with authoritative readback and compare-fences every undo.
   A foreign, unreadable, or ambiguous current state is contained without destructive rollback;
7. starts or reuses one exact production supervisor under `~/.aar/codex` from the explicit setup
   process. Normal Codex task startup remains a fast attach path instead of owning the supervisor.

The command prints a JSON receipt. A passing receipt must contain `status: "passed"`, dependency
versions, `tool_count: 30`, `workspace_closed: true`, `declared_args: []`,
`supervisor.mode: "attached-supervisor"`, `supervisor.frontend_ephemeral: true`, the installed
plugin version, the exact `aar-codex-mcp` launcher, and a `codex_runtime` object with
`status: "ready"`, runtime and dispatcher generations, discovery digest, and supervisor version.
`mcp_authority: "plugin"`, `marketplace_replaced`, `legacy_global_mcp_removed`,
`configuration_changed`, and `plugin_changed` state what was selected or written.

If production startup fails after the desired plugin-only configuration has passed final readback,
setup reports `status: "configuration_committed_runtime_unready"`. That is retry-safe retained
configuration, not setup success and not rollback; rerun `aar-codex-setup` to recover the production
owner. A structured transaction-containment error means setup refused to overwrite state it could
not prove it owned. Inspect the reported normalized current state before retrying or changing Codex
configuration.

Restart Codex App only when `restart_required` is true, then call `aar_capabilities` in a fresh
task. A no-op rerun reports `restart_required: false`. Config, catalog, an embedded `--database`
probe, or setup output alone is not fresh-host runtime proof. Codex versions that defer MCP tools
may require native tool search to load the exact `mcp__aar__aar_capabilities` name. The search
result is not evidence; invoke the loaded tool and verify its package, tool-surface, skill,
supervisor-mode, and runtime-generation fields.

If setup or the launcher reports that native process identity is unavailable, do not delete the
runtime-home control files and do not start a second supervisor manually. Retry after the native
identity observation recovers; uncertainty is intentionally not treated as process absence.

Useful bounded variants:

```powershell
aar-codex-setup --preflight-only
aar-codex-setup --skip-preflight
aar-codex-setup --stop-runtime
aar-codex-setup --help
```

`--skip-preflight` is intended for a repeated configuration-only repair after the same installed
wheel has already passed. It is not the default.

`--stop-runtime` targets only the exact live process identity discovered under the stable Codex
runtime home (`~/.aar/codex` by default). It does not stop a Hermes, WSL, or operator-managed AAR
supervisor. Use `--codex-runtime-home <path>` only for an intentionally separate Codex runtime.

The setup-provisioned supervisor normally survives a replaceable frontend because it is not a
child of that task. If setup was skipped or the owner was lost, `aar-codex-mcp` can self-heal by
starting a successor. A host may terminate that fallback child with its descendant tree. Treat
that as hard owner loss: never reuse stale discovery, and require a successor-generation readback
instead of claiming same-process continuity.

Setup inspects `~/.codex/config.toml` by default. Use `--codex-config <path>` only when the Codex
host intentionally uses another config file. The legacy entry is recognized from the actual
`[mcp_servers.aar]` table, not from `codex mcp get`, because that command can also display the
correct plugin-provided server.

## Maintainer verification is separate

Release maintainers additionally run Ruff, focused regressions, the full repository suite, Python
3.11–3.14 compatibility, exact-wheel readback, and fresh Codex scenarios. General users do not
need those development and release gates to install AAR.

The setup command does not grant provider credentials, external effects, activation, publication,
or final delivery. AAR computes and proposes; the host authorizes and delivers.
