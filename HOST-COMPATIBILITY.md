# Host Compatibility

Adaptive Agent Harness (AAR) exposes a host-neutral Python contract and a local-stdio MCP server. Host integration is packaging and lifecycle glue around that public surface; it does not grant provider credentials, external-effect authority, or final-delivery authority to AAR.

## Supported baseline

| Surface | Public support in `v0.3.0a2` | Verification path | Important boundary |
|---|---|---|---|
| Python package | CPython 3.11–3.14 | `uv sync --locked`, repository tests, exact-wheel install | Python execution is not a security sandbox |
| MCP client | Local stdio, 30-tool `aar.mcp-tools.v7` surface | `aar-mcp`, `aar_capabilities`, generated schema verification | Transport success is not operation success |
| Codex host profile | Bundled plugin, canonical `aar-operations` skill, setup helper | `aar-codex-setup --preflight-only` followed by a fresh client process | The host owns approvals, credentials, and tool policy |
| Hermes host profile | Bundled profile, canonical `aar-operations` skill, ordinary MCP registration | configure `aar-mcp`, start the durable supervisor, then run `hermes mcp test aar` | The host owns MCP Sampling and any physical provider request |
| Direct embedding | Pydantic contracts, reference host, supervisor/frontend APIs | import package APIs and run contract tests | Integrators must preserve identity, budget, generation, and receipt semantics |

The canonical operation skill is host-neutral. Generated Codex and Hermes copies must be byte-identical to `skills/aar-operations/SKILL.md`; host profiles may describe installation, but may not fork operation semantics.

## General MCP installation

Install an immutable wheel or a reviewed source checkout:

```bash
uv tool install adaptive-agent-runtime
```

Start the local stdio frontend:

```bash
aar-mcp --runtime-home "$HOME/.local/state/adaptive-agent-harness"
```

For a durable deployment, run one supervisor separately and let replaceable MCP frontends attach to its signed discovery record:

```bash
aar-supervisor \
  --runtime-home "$HOME/.local/state/adaptive-agent-harness" \
  --programmable-backend ipython \
  --transport unix \
  --dispatcher-concurrency 1
```

Use an owner-private runtime directory. Do not place attachment credentials, sockets, databases, or discovery records in a group- or world-writable directory.

## Codex integration

The bundled Codex profile installs the canonical operation skill and points the host at the exact tool-environment executable. See [Codex installation](docs/CODEX-INSTALL.md).

A minimal verification sequence is:

```bash
aar-codex-setup --preflight-only
codex mcp get aar
```

Restart or open a fresh Codex process after changing the tool installation or plugin. A process that already loaded an older MCP child is not proof that the new package is active.

Codex approval settings remain host policy. A successful approval or tool call does not prove external-effect execution or delivery unless the authoritative host records that outcome.

## Hermes integration

Register the stable AAR tool entrypoint in the Hermes MCP configuration:

```yaml
mcp_servers:
  aar:
    command: /absolute/path/to/aar-mcp
    args:
      - --runtime-home
      - /absolute/path/to/private/runtime-home
    connect_timeout: 60.0
```

Run the supervisor as a user service or another owner-controlled process. After installation or upgrade, verify both layers:

```bash
# Read back package and skill identity.
python - <<'PY'
from importlib.metadata import version
from aar.mcp.server import OPERATION_SKILL_VERSION
print(version("adaptive-agent-runtime"))
print(OPERATION_SKILL_VERSION)
PY

# Verify that Hermes starts a fresh frontend and discovers the public surface.
hermes mcp test aar
```

A passing `v0.3.0a2` readback reports package `0.3.0a2`, operation skill `0.9.0`, and 30 MCP tools.

### Receipt-backed model routing

AAR can expose the explicit owner-controlled route profile:

```bash
aar-mcp \
  --database /path/to/run.sqlite3 \
  --programmable-backend plain \
  --hermes-mcp-sampling-luna-max
```

This embedded reference-host mode binds `openai-codex / gpt-5.6-luna / max`. The Hermes MCP client must own the physical provider call and return the `aar.model-receipt.v1` extension. AAR rejects missing, malformed, drifted, retried, or fallback receipts under the strict profile.

This option proves a bounded integration path; it is not a general provider configuration API. See [Receipt-backed model routing and fair evaluation](docs/MODEL-ROUTING-AND-EVALUATION.md).

## Direct embedding

Direct consumers should preserve these invariants:

- validate exact schema versions and deny unknown fields;
- bind principal, session, runtime generation, capability digest, deadline, budget, grant, request identity, and idempotency identity;
- commit operation intent before dispatch;
- retain authoritative broker receipts and provider usage;
- classify post-send uncertainty as `indeterminate` unless an authoritative receipt resolves it;
- reconcile before replaying state-changing or provider-backed work;
- keep activation, external effects, and final delivery in the host.

The reference host is an executable conformance implementation, not a multi-tenant security boundary.

## Upgrade verification

An upgrade is active only when all applicable identities agree:

1. the installed distribution reports the expected package version;
2. the executable resolves inside that installed environment;
3. a newly started supervisor reports the expected version and a new runtime generation;
4. a fresh MCP frontend connects and lists the expected tools;
5. generated schemas, profiles, and skill metadata verify;
6. any provider-backed qualification reads back the exact effective route, provider usage, retry/fallback state, and durable outcome.

Do not infer activation from a wheel existing on disk. Do not infer model-route eligibility from a CLI flag alone.

## Known limitations

- AAR is not a security sandbox and does not provide multi-tenant isolation.
- MCP Sampling is deprecated in protocol revision `2026-07-28` (SEP-2577). `v0.3.0a2` retains a bounded compatibility path for hosts that still support the bidirectional Sampling back-channel; future integrations should migrate to a replacement host-owned broker transport when standardized.
- There is no portable provider lookup API for recovering a response after transport loss. Unresolved post-send outcomes remain indeterminate or quarantined.
- Package installation does not create an operating-system service, register a host, or configure credentials.
- Generated host profiles do not change AAR's authority ceiling.
- Published compatibility covers the public contract and documented setup paths, not every host version or platform configuration.
