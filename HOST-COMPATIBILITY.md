# Host Compatibility

Adaptive Agent Harness (AAR) exposes a host-neutral Python contract and a local-stdio MCP server. Host integration is packaging and lifecycle glue around that public surface; it does not grant provider credentials, external-effect authority, or final-delivery authority to AAR.

## Supported baseline

| Surface | Public support in `v0.4.0a0` | Verification path | Important boundary |
|---|---|---|---|
| Python package | CPython 3.11–3.14 | `uv sync --locked`, repository tests, exact-wheel install | Python execution is not a security sandbox |
| MCP client | Local stdio, 30-tool `aar.mcp-tools.v7` surface | `aar-mcp`, `aar_capabilities`, generated schema verification | Transport success is not operation success |
| ChatGPT / Codex public plugin candidate | OAuth-authenticated Streamable HTTP with six tenant-workspace and five caller-delegated RLM tools | deterministic plugin packet, exact-wheel HTTP tests, fresh local Codex lifecycle | GitHub release and local use are not production deployment or Plugin Directory publication |
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

A passing `v0.4.0a0` local-profile readback reports package `0.4.0a0`, operation skill `0.9.1`, and 30 MCP tools.

### Receipt-backed model routing

AAR can expose the explicit owner-controlled route profile:

```bash
aar-mcp \
  --database /path/to/run.sqlite3 \
  --programmable-backend plain \
  --hermes-mcp-sampling-luna-max
```

This embedded reference-host mode binds `openai-codex / gpt-5.6-luna / max`. The Hermes MCP client must own the physical provider call and return the `aar.model-receipt.v1` extension. AAR rejects missing, malformed, drifted, retried, or fallback receipts under the strict profile.

This option is deprecated compatibility behavior and is not a general provider configuration API.
New integrations should use a host-owned broker or the public caller-delegated RLM lifecycle below.
See [Receipt-backed model routing and fair evaluation](docs/MODEL-ROUTING-AND-EVALUATION.md).

## Public ChatGPT and Codex plugin candidate

`aar-mcp-public` is a separate remote product adapter. It validates an asymmetric OAuth access
token, derives an opaque issuer/subject tenant key, and leases one persisted tenant runtime. Its
eleven-tool surface excludes arbitrary Python, provider credentials and endpoints, service-managed
provider calls, external effects, activation, and delivery.

For one RLM job, the main agent selects one model and optional reasoning effort. AAR persists the
fixed route and exact prompts, issues pre-spend claim tickets, and accepts only ticket-bound bounded
commits; the host executes every actual model call. Restart and replay preserve the same route and
idempotency boundaries. A different task route requires a different job.

The release candidate passed exact-wheel HTTP/authentication/isolation/restart tests, deterministic
plugin and scoped-skill generation, and a fresh local Codex marketplace-derived lifecycle with an
actual `gpt-5.6-sol` / `max` host call. The resulting provenance is intentionally
`caller_reported`, not provider-signed. A final independent review covered 77 required release cells
and reported no actionable findings.

This row does not claim a production HTTPS deployment, public OAuth flow, reviewer execution,
ChatGPT execution, OpenAI approval, or Plugin Directory publication. Those require a deployed
endpoint, public policies and controls, reviewer credentials, portal scan, review, approval, and a
separate publisher action. See [Public plugin and submission boundary](docs/PUBLIC-PLUGIN.md).

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

- AAR is not a security sandbox. The public structured-state adapter separates authenticated tenant databases, but the local programmable-execution surface is not a hostile multi-tenant isolation boundary.
- MCP Sampling is deprecated in protocol revision `2026-07-28` (SEP-2577). `v0.4.0a0` retains a bounded compatibility path for existing hosts; new public RLM integrations use caller-delegated model execution.
- There is no portable provider lookup API for recovering a response after transport loss. Unresolved post-send outcomes remain indeterminate or quarantined.
- Package installation does not create an operating-system service, register a host, or configure credentials.
- Generated host profiles do not change AAR's authority ceiling.
- Published compatibility covers the public contract and documented setup paths, not every host version or platform configuration.
- The `v0.4.0a0` Windows workstation passed the focused public-plugin and local Codex gates, but six legacy Sampling/process-owner lifecycle tests remain red in the full Windows suite; full Windows supervisor support is not a release claim.
