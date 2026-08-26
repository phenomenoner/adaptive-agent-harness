# Hermes provider-ready host adapter

AAR `0.6.0a1` adds an opt-in Hermes launcher for clean-installed provider-ready runtimes. It keeps
the MCP tool surface unchanged while giving the local host an explicit way to issue short-lived,
memory-only session grants.

## What it adds

- `aar-hermes-mcp` starts or reuses one exact provider-ready supervisor, verifies the current
  activation and route catalog, probes the live MCP server, then attaches the stdio frontend.
- `aar-hermes-authority issue` explicitly issues one policy-bounded grant for one principal,
  session, capability, and TTL.
- `aar-hermes-authority revoke` ends a current-process grant before expiry.

The launcher never issues a grant. Install, startup, Ready publication, MCP attach, and an ordinary
mutation request all remain grant-free.

## Prerequisites

Prepare these host-owned inputs before configuring Hermes:

1. an exact `0.6.0a1` wheel;
2. a new, previously absent runtime home installed through `aar-admin runtime install`;
3. the matching host activation intent, candidate receipt, and route catalog;
4. a successful read-only `aar-admin activation verify` result.

Do not point this adapter at an older runtime home. Clean installation means creating a new root; it
does not adopt, promote, archive, remove, or rewrite an old root.

The standalone adapter accepts two explicit route drivers:

- `reference-driver` with provider `reference`, only for deterministic qualification;
- `host-caller-driver-v1` for real provider work through the durable caller-work protocol.

A caller-delegated route never performs a server-owned provider send. Hermes claims the ticket,
marks the conservative may-have-sent boundary, performs the physical model call with its configured
provider authority, and commits the exact response/usage receipt. If that flow is bypassed, the
standalone broker fails closed. Do not relabel a reference route as a real provider route.

## Hermes MCP configuration

Use the installed entry point and exact host paths:

```yaml
mcp_servers:
  aar:
    command: /absolute/path/to/venv/bin/aar-hermes-mcp
    args:
      - --runtime-home
      - /absolute/path/to/new-runtime-home
      - --route-catalog
      - /absolute/path/to/route-catalog.json
      - --default-route-profile
      - route-primary
    connect_timeout: 30
    timeout: 60
```

On startup, the adapter requires all of these readbacks to agree:

- package and supervisor version;
- process identity, supervisor protocol version/digest, and runtime/dispatcher generations;
- attached-supervisor mode and capability digest;
- current provider-ready activation grant set;
- route-catalog digest and default route profile.

A mismatch fails closed before the frontend is handed to Hermes.

## Explicit grant workflow

Issue only the capability needed for the next bounded operation:

```bash
/absolute/path/to/venv/bin/aar-hermes-authority \
  --runtime-home /absolute/path/to/new-runtime-home \
  issue \
  --principal-id principal-local \
  --session-id session-example \
  --capability rlm.workbench.execute \
  --ttl-ms 60000 \
  --grant-id grant-example-rlm-1
```

The successful response is the complete immutable grant document. Copy its exact grant ID and
published budget ceilings into the matching MCP context. Every provider-ready mutation requires this
kind of current, exact session grant; `aar_reference_context` and static reference grants cannot
supply provider-ready authority. The command does not authorize a provider credential, external
effect, activation change, or final delivery.

Revoke a still-live grant explicitly:

```bash
/absolute/path/to/venv/bin/aar-hermes-authority \
  --runtime-home /absolute/path/to/new-runtime-home \
  revoke \
  --grant-id grant-example-rlm-1
```

Exit status meanings:

- `0`: terminal issued or revoked receipt returned;
- `2`: terminal rejection; no success receipt exists;
- `3`: outcome indeterminate after send. Preserve the reported grant ID and do not reuse it;
- `1`: local/pre-send failure.

## Restart and rollback

Every supervisor restart destroys the live grant map. Refresh `aar_capabilities`, use the successor
runtime generation, and issue a new grant through a new explicit command. A prior grant ID must not
be accepted after restart.

Rollback changes only the Hermes MCP command and arguments back to the retained prior runtime. It
does not mutate either runtime root. Verify rollback from a fresh native `aar_capabilities` response;
a config file or launcher probe alone is not runtime evidence.

## Boundaries

This remains an alpha host integration. It does not add a public grant tool, persistent grant store,
provider credential path, effect executor, or automatic authorization policy. IPython executes
model-authored Python and is not a security sandbox.
