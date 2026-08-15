# Adaptive Agent Harness v0.4.0a4 — Codex lifecycle repair alpha

`v0.4.0a4` is a maintenance alpha for the local Codex plugin path. It preserves the caller-
delegated public RLM product introduced in `v0.4.0a0` and repairs a false-positive installation
check that could report success even though a fresh Codex task could not start AAR.

## What changed

- The plugin now declares `aar-codex-mcp` with empty arguments. This host adapter starts or reuses
  one exact durable supervisor, then attaches the replaceable stdio frontend.
- `aar-codex-setup` runs the manifest's exact command and arguments in its real
  attached-supervisor mode. An embedded `aar-mcp --database` lifecycle cannot satisfy preflight.
- Setup binds `aar-local` to the marketplace bundled in the installed wheel and replaces a
  same-name entry rooted elsewhere, even when its version string matches.
- Explicit setup provisions the stable production supervisor before Codex starts. Concurrent first
  launchers converge on one owner, upgrades use an identity-bound shutdown request, and a launcher
  refuses to attach to a live supervisor from another package version.
- The canonical `aar-operations` `0.9.4` workflow keeps the existing host-owned model-route and
  caller-delegated RLM guidance while adding exact launch, marketplace-root, deferred-tool, and
  fresh-native-call verification rules.

## Install or upgrade

Close Codex App before replacing a live Windows tool environment. For upgrades from `0.4.0a4` or
later, the setup helper can first stop only the exact Codex-owned runtime:

```powershell
aar-codex-setup --stop-runtime
uv tool install --force "git+https://github.com/phenomenoner/adaptive-agent-harness.git@v0.4.0a4"
aar-codex-setup
```

Restart Codex only when setup reports `restart_required: true`. Then create a fresh task and make
one native `aar_capabilities` call. If Codex defers MCP tools, search only loads the exact tool; the
subsequent MCP response is the proof.

## Verification

- 36 focused launcher, setup, package, profile, concurrency, and shutdown tests;
- exact-wheel entrypoint, RECORD, bundled-profile, and declared-command preflight checks;
- a restarted Codex Desktop fresh task reading package `0.4.0a4`, skill `0.9.4`, 30 tools,
  `attached-supervisor`, and an ephemeral frontend from the native capability response;
- full Python 3.11–3.14 Linux GitHub Actions repository matrix on the tagged commit.

## Boundaries

- AAR remains a programmable runtime, not a security sandbox.
- The host still owns provider credentials, physical model calls, external-effect authorization,
  activation, and final delivery.
- Six disclosed Windows Sampling/process-owner lifecycle tests remain outside the verified local
  Codex launcher path and prevent a full Windows supervisor-suite claim.
- A GitHub release and verified local Codex installation are not production deployment, ChatGPT
  execution, OpenAI review, approval, or official Plugin Directory publication.

See [Codex installation](CODEX-INSTALL.md), [Host compatibility](../HOST-COMPATIBILITY.md), and the
[changelog](../CHANGELOG.md) for exact operational boundaries.
