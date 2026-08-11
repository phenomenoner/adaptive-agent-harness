# AAR maintainer test cadence

Choose tests from the claim being changed. Historical phase evidence describes what was accepted at
that candidate; it is not a command list to replay after every edit.

## Edit loop

Run the narrowest check that would fail for the current change, normally on the active interpreter:

```powershell
uv run --locked pytest -q tests/test_codex_setup.py
uv run --locked ruff check src/aar/compat/codex_setup.py tests/test_codex_setup.py
```

Replace those paths with the affected module and tests. Add a real seam only when the claim crosses
it:

- `tests/test_codex_setup.py` contains one real MCP/supervised-worker dependency preflight plus
  configuration unit cases.
- `tests/test_compat_smoke.py` is the reusable broad MCP lifecycle; run it when its lifecycle,
  dependency, tool-surface, or smoke contract changes, not for every installer wording change.
- `tests/test_package_assets.py` checks package metadata at T0. It does not prove built wheel bytes.
- `tests/test_mcp_server.py` and the process/recovery tests are selected for their specific MCP or
  lifecycle behavior; they are not a generic completion stamp.

## Candidate order

When the requested claim really needs a package or host candidate:

1. stabilize executable source and focused tests;
2. freeze package metadata, bundled documentation, host profiles, and plugin version;
3. run the touched supported-Python slice only when interpreter/dependency compatibility is in the
   claim;
4. run the full repository suite once only for a release gate or repository-wide blast radius;
5. build one final wheel and preserve that exact instance;
6. install/read back that wheel and run only the isolated interpreter extremes needed by the claim;
7. run one fresh-host scenario against the exact installed artifact and configuration.

Do not build a wheel before packaged bytes are frozen, run another full suite because only evidence
prose changed, or repeat a fresh-host scenario against unchanged artifact/configuration bytes.

For `codex exec` compatibility probes, record the host sandbox and approval policy as part of the
evidence. With `codex-cli 0.146.0`, a `read-only` sandbox did not expose the AAR plugin's mutation
tools; `workspace-write` exposed them, while the probe itself still used no shell or repository
writes. A non-interactive host may cancel `aar_program_workspace_close` because it is correctly
annotated as destructive. Treat that as a host-approval result, not an AAR success or failure, and
do not retry blindly. When a later wheel changes only installer/profile bytes, compare exact wheel
members before reusing unchanged MCP/runtime lifecycle evidence.

## Evidence reuse

| Change since a passing result | What is invalidated |
|---|---|
| Source or relevant test/contract | Focused behavior evidence for the affected surface |
| Dependency range or lock resolution | Dependency/import checks and any claimed interpreter slice |
| MCP schema, profile, plugin, or launcher | Corresponding asset/config check and claimed host row |
| Packaged bytes or build recipe | Exact-wheel identity/readback only |
| Documentation, WAL, receipt, or formatting outside the package | No executable test evidence |

Use `uv lock --check`, `git diff --check`, and focused asset verifiers only when their inputs changed.
General users install with `uv tool install` plus `aar-codex-setup`; they do not run this maintainer
cadence.
