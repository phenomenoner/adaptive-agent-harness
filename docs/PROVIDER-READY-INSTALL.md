# Provider-ready clean installation

Adaptive Agent Runtime is host-neutral. Any compatible agent harness can install a fresh
provider-ready runtime; Hermes and Codex integrations are optional host adapters, not AAR authority.

This path is **clean-install only**. The runtime target must not exist. None of the input-producing
commands below installs a package, activates a runtime, issues a session grant, calls a provider, or
modifies another runtime root.

## Inputs

Prepare:

- an exact released `adaptive_agent_runtime-<version>-*.whl`;
- the exact 40-character source commit for that release;
- an absent absolute runtime-home path whose parent already exists;
- a host-owned intent template containing only:
  `profile_id`, `planner`, `adapters`, `routes`, `grant_policy`,
  `cutover_authority_store_id`, and `recovery_compatibility_digest`.

`docs/examples/provider-ready-intent-template.json` is a strict structural example. The installing
host must review the host-owned planner, adapter selection, grant policy, authority-store, recovery,
and allowed-route values. The issuer binds package-owned adapter/factory/schema identities to the
candidate receipt, binds the route-policy digest to the exact `--route-catalog`, and binds runtime
identity to the absent `--runtime-home`; the template cannot override those values. Provider
credentials and session grants do not belong in this document.

`docs/examples/provider-ready-route-catalog.json` is a caller-delegated structural example. A real
host must supply its reviewed provider, model, reasoning, fallback, and cache policy without placing
credentials in the catalog.

## 1. Issue the exact candidate receipt

```text
aar-admin runtime candidate \
  --wheel /absolute/path/adaptive_agent_runtime-0.6.0a1-py3-none-any.whl \
  --source-commit <40-lowercase-hex-release-commit> \
  > /absolute/path/install-candidate-receipt.json
```

The command validates the wheel ZIP shape, fixed schema/fixture/skill assets, fixture digests, and
package factory members before emitting `aar.install-candidate-receipt.v1`. The receipt binds the
exact wheel bytes and the explicit source-commit value. For an official release, independently
compare that value with the tag target and release receipt; a wheel cannot prove its Git source by
itself.

## 2. Issue a target-bound generation-1 intent

```text
aar-admin activation intent \
  --runtime-home /absolute/new/runtime-root \
  --candidate-receipt /absolute/path/install-candidate-receipt.json \
  --route-catalog /absolute/path/provider-ready-route-catalog.json \
  --template /absolute/path/host-intent-template.json \
  > /absolute/path/host-activation-intent.json
```

The command requires an absent target, derives the canonical runtime-home digest and database
identity, forces registry v6, IPython, `trusted_local`, activation generation 1, and a null previous
authority, then calls the existing `HostActivationIntent.issue` contract. Unknown template fields,
invalid nested contracts, an existing target, symlinks, or an unsafe parent fail closed.

## 3. Install once

```text
aar-admin runtime install \
  --runtime-home /absolute/new/runtime-root \
  --intent /absolute/path/host-activation-intent.json \
  --candidate-receipt /absolute/path/install-candidate-receipt.json \
  --wheel /absolute/path/adaptive_agent_runtime-0.6.0a1-py3-none-any.whl
```

The installer stages below the retained parent directory and publishes with no-replace semantics.
It never preserves, adopts, promotes, archives, removes, or rewrites an old runtime root. If the
result is `PUBLICATION_INDETERMINATE`, do not retry blindly; reconcile the exact target and retained
receipt first.

## 4. Read back the installed authority

```text
aar-admin activation verify \
  --runtime-home /absolute/new/runtime-root \
  --profile /absolute/new/runtime-root/authority/profile.json

aar-admin activation status --runtime-home /absolute/new/runtime-root
```

A successful install is still not a live host. Start the release's standalone supervisor/host
adapter, read back the exact process/protocol/generation/capability/activation/route identity, then
have the host explicitly issue only the bounded memory-only session grants needed by the current
session. Startup, Ready, attachment, reference-context, and ordinary MCP requests never issue those
grants.

## Host provider ownership

For `host-caller-driver-v1`, the installing host claims a durable caller-work ticket, commits
`mark-send-started` immediately before its physical provider call, and commits or reconciles the
exact provider response and usage receipt. AAR never receives provider credentials and its
service-owned model broker fails closed if asked to execute a caller-owned route.

Before a caller-work mutation, read the operation's first `intent_persisted` event and derive the
exact cumulative deadline as `min(original context deadline, intent_persisted.at_unix_ms + requested
total wall time)`. This value is reproducible from existing public bytes and is the deadline CAS
witness for claim, send-start, cancel, commit, and reconcile; do not read the runtime database.
