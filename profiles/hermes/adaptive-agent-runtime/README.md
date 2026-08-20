# Hermes host profile

Release package: `0.5.0a0`; bundled operation skill: `0.10.0`. The
source repository's immutable release snapshot is `profiles/release-status-v1.json`; exact
post-freeze evidence is bound by
`adaptive-agent-runtime-v0.4.0a6-release-receipt.json`. This profile does not establish official
Plugin Directory publication, which requires separate external authority.
Install the exact `adaptive-agent-runtime` wheel with `uv tool install --force <wheel>` so `aar-mcp`
and its declared IPython, NumPy, and pandas dependencies are on the Hermes host PATH, then install
this directory with `hermes profile install <directory> --name <profile>`. The runtime reads
`config.yaml.mcp_servers`; `mcp.json` is the equivalent reviewable server map.

The profile provides ordinary MCP operations and host-owned RLM model calls. It contains no provider
credentials and does not authorize billable inference. A Hermes client may own the physical call and
return an `aar.model-receipt.v1` receipt.

Supervisor endpoint, credential, and shutdown-request paths are generation-unique; the stable
discovery pointer is advanced atomically. Normal lifecycle cleanup is deliberately non-destructive,
so generation-specific control artifacts remain forensic state. The subprocess Codex setup route is
not Hermes authority and reports `NO_ATOMIC_AUTHORITY`
when no provider revision/CAS contract exists; follow the ordered manual plan through the host's
configuration owner. After an upgrade, restart Hermes if required and create a fresh task/session
before relying on capability discovery. See `$aar-operations` for the full operation workflow.
