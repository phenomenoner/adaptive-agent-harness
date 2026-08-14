# Adaptive Agent Harness v0.4.0a0

`v0.4.0a0` is a public alpha release of the Adaptive Agent Runtime package and its
official-Plugin-Directory candidate. It retains the local 30-tool developer MCP surface and adds a
separate eleven-tool OAuth-authenticated remote profile for ChatGPT and Codex.

## What changed

- six tenant-private structured-workspace tools;
- five caller-delegated RLM tools: start, pre-spend claim, ticket-bound commit, status, and cancel;
- one main-agent-selected executor, model, and optional reasoning effort for each job;
- durable prompts, tickets, bounded caller observations, idempotency, restart, concurrency, and
  terminal-key conflict handling;
- a deterministic plugin tree, scoped `aar-public-runtime` skill ZIP, five positive and three
  negative reviewer cases, brand assets, and a non-root container profile;
- `aar-operations` `0.9.1`, combining the existing host-owned model-route guidance with the public
  caller-delegated lifecycle and retry boundary.

AAR never receives provider credentials or endpoints for the public RLM path. The authenticated
main agent or host executes every actual model call and commits only the route, output, usage, and
optional receipt data it really observed.

## Install the local developer profile

Pin the immutable tag:

```bash
uv tool install --force \
  "git+https://github.com/phenomenoner/adaptive-agent-harness.git@v0.4.0a0"
aar-codex-setup
```

Restart Codex after a changed setup and call `aar_capabilities` from a fresh task. This installs the
local developer profile, not the remote public-directory profile.

## Verification

The candidate passed:

- locked dependency, generated contract, MCP asset, host-profile, bundled-skill, Ruff, and full
  Python 3.11–3.14 Linux CI repository checks;
- exact-wheel public HTTP/authentication, isolation, quota, restart, idempotency, concurrency, and
  RLM lifecycle tests;
- deterministic plugin and scoped-skill generation;
- a fresh local Codex marketplace-derived run that persisted workspace state and completed an
  actual caller-delegated `gpt-5.6-sol` / `max` model call;
- an independent final review of 77 required cells with no actionable findings.

The Windows release workstation passed the focused public-plugin/package and fresh local Codex
gates. Its full repository run retains six known failures in legacy Sampling/process-owner lifecycle
tests, so this release does not claim a full Windows supervisor-suite pass.

The release artifacts were built from commit
`2b6862e0a72eb6365faf90fa5314e0b9ccb144bf` (tree
`b00c6d12499a156f404de0ec466a350144045f07`):

- `adaptive_agent_runtime-0.4.0a0-py3-none-any.whl` — 454,411 bytes, SHA-256
  `17c7a85f1c20045203b75c98762e5d9c35f687a376e2ab9a2967a0d868450625`;
- `aar-public-runtime-skill-0.4.0.zip` — 6,604 bytes, SHA-256
  `39bf6f2c6c010f9518c7fcad0f5da6417f040048ea577df5874e10dd153fcbdf`.

The source tree and the installed exact wheel generated identical 13-file plugin templates with
manifest SHA-256 `4b3c40ae48dd99ffa5efd2a63f1c6fb4b1ad795a626e897b0518573854e0e613`.
The template used non-production `vendor.dev` URLs only for deterministic local validation and is
not attached as a submission-ready asset. The GitHub release attaches the wheel and URL-independent
scoped skill ZIP with matching sidecars. Verify downloaded files before use.

## Publication boundary

This GitHub release is submission material. It is not an official Plugin Directory listing.
Production HTTPS/OAuth, reviewer credentials and execution, public policy and operational controls,
fresh ChatGPT execution, portal scan, OpenAI review, approval, and the publisher's final publish
action remain external gates.

Read [Public plugin and submission boundary](PUBLIC-PLUGIN.md),
[Caller-delegated RLM design](PUBLIC-RLM-PRODUCT-BOUNDARY.md),
[Security](../SECURITY.md), and [Host compatibility](../HOST-COMPATIBILITY.md) before deploying or
making compatibility claims.
