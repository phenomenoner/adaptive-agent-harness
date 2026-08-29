# Hermes host profile

Release package: `0.6.0a1`; bundled operation skill: `0.11.0`. The
source repository's release contract is `profiles/release-status-v1.json`; it does not establish
that the target tag, GitHub prerelease, or `adaptive-agent-runtime-v0.6.0a1-release-receipt.json` exists. Publication and exact
post-freeze evidence require external readback. This profile does not establish official
Plugin Directory publication, which requires separate external authority.
Use a new isolated Hermes home plus new UV tool and bin directories. Install the exact
`adaptive-agent-runtime` wheel with `uv tool install <wheel>` so `aar-hermes-mcp` and its declared
IPython, NumPy, and pandas dependencies are available only to that fresh home, then install this
directory with `hermes profile install <directory> --name <profile>`. Set the three required profile
values to the reviewed absolute new runtime home, host-owned route catalog, and allowed default
route profile. The runtime reads `config.yaml.mcp_servers`; `mcp.json` is the equivalent reviewable
server map. Unset placeholders remain literal and the launcher fails closed. This release does not
upgrade, replace, reconfigure, cut over, or roll back an existing Hermes home or runtime root;
switching any live integration pointer is outside this release.

The profile provides ordinary MCP operations and host-owned RLM model calls. It contains no provider
credentials and does not authorize billable inference. A Hermes client may own the physical call and
return an `aar.model-receipt.v1` receipt.

Supervisor endpoint, credential, and shutdown-request paths are generation-unique; the stable
discovery pointer is advanced atomically. Normal lifecycle cleanup is deliberately non-destructive,
so generation-specific control artifacts remain forensic state. The subprocess Codex setup route is
not Hermes authority and reports `NO_ATOMIC_AUTHORITY`
when no provider revision/CAS contract exists; follow the ordered manual plan through the host's
configuration owner. After the fresh clean installation, restart that isolated Hermes instance if
required and create a fresh task/session
before relying on capability discovery. See `$aar-operations` for the full operation workflow.
